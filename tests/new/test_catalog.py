"""Table enrollment changes visibility while preserving physical data and past results."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from enterprise_agent.flow.application import Application
from .postgres_fixture import (
    TestStores as Stores,
    identity_scope,
    cleanup,
    namespace,
    test_dsn,
)
from enterprise_agent.ledger.postgres import Postgres
from .fixtures import FixtureModel


class CatalogModel(FixtureModel):
    @staticmethod
    def definition(context):
        result = FixtureModel.definition(context)
        if context["operation"]["instruction"] == "查询新增表":
            result["parameters"]["target"] = "extrafacts"
        return result

    def run(self, role, instruction, context, output, tools=None, emit=None):
        if context.get("parameters", {}).get("target") != "extrafacts":
            return super().run(role, instruction, context, output, tools, emit)
        if role == "SemanticAgent":
            self.calls.append(role)
            return output.model_validate(
                {
                    "status": "bound",
                    "tables": ["extrafacts"],
                    "fields": ["extrafacts.fact_id", "extrafacts.value"],
                    "explanation": "新增表真实字段",
                }
            )
        if role == "QueryAgent":
            self.calls.append(role)
            saved = tools["execute_readonly_sql"].call(
                sql="SELECT fact_id, value FROM extrafacts ORDER BY fact_id",
                title="新增表",
                summary="真实新增表数据",
            )
            return output.model_validate(
                {
                    "summary": "新增表已查询",
                    "produced_outputs": {
                        "dataset": {"kind": "artifact", "id": saved["artifact_id"]},
                    },
                }
            )
        return super().run(role, instruction, context, output, tools, emit)


class Catalog(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.addCleanup(cleanup)
        self.source = namespace(self.path, "_source")
        self.source_database = Postgres(test_dsn(), self.source)
        self.source_database.setup(
            1,
            [
                "CREATE TABLE products(product_id INTEGER PRIMARY KEY,product_name TEXT)",
                "INSERT INTO products VALUES(1,'A'),(2,'B')",
                "CREATE TABLE extrafacts(fact_id INTEGER PRIMARY KEY,product_id INTEGER REFERENCES products(product_id),value DOUBLE PRECISION)",
                "INSERT INTO extrafacts VALUES(1,1,12.5),(2,2,20.0)",
            ],
        )
        self.plugin_patch = self.path / "source.yaml"
        self.plugin_patch.write_text(
            "plugins:\n  - id: database.readonly\n    config:\n      dsn: "
            + json.dumps(test_dsn())
            + "\n      schema: "
            + self.source
            + "\n",
            encoding="utf-8",
        )

    def application(self):
        engine = Application(
            Stores(self.path / "work"),
            CatalogModel(),
            plugin_patches=[self.plugin_patch],
        )
        self.addCleanup(engine.close)
        return engine

    def test_discover_enroll_and_query_a_new_table_through_ask(self):
        engine = self.application()
        self.assertNotIn("extrafacts", engine.registry.tables)
        discovered = engine.available_tables()["tables"]
        self.assertIn(
            {"name": "extrafacts", "kind": "table", "registered": False}, discovered
        )
        before = engine.registry.catalog_version
        engine.change_tables(
            "enroll",
            name="extrafacts",
            description="新增事实",
            column_meanings={"value": "实际值"},
        )
        self.assertNotEqual(engine.registry.catalog_version, before)
        self.assertEqual(
            engine.registry.tables["extrafacts"]["columns"]["value"]["meaning"],
            "实际值",
        )
        result = engine.ask(1, "查询新增表")
        self.assertEqual(result["status"], "completed", result["blocks"])
        query = next(
            item for item in result["artifacts"] if item["content_type"] == "table"
        )
        self.assertEqual(
            engine.artifacts.content(query["artifact_id"]),
            [{"fact_id": 1, "value": 12.5}, {"fact_id": 2, "value": 20.0}],
        )
        self.assertEqual(
            query["payload"]["provenance"]["catalog_version"],
            engine.registry.catalog_version,
        )

    def test_removal_blocks_new_queries_and_preserves_history(self):
        engine = self.application()
        result = engine.ask(1, "列出前五个产品")
        self.assertEqual(result["status"], "completed")
        query = next(
            item for item in result["artifacts"] if item["content_type"] == "table"
        )
        content = engine.artifacts.content(query["artifact_id"])
        engine.change_tables("remove", name="products")
        self.assertNotIn("products", engine.registry.overview()["tables"])
        binding = {"status": "bound", "tables": ["products"], "relations": []}
        with self.assertRaisesRegex(ValueError, "移出问析目录"):
            engine.query_gateway.execute("SELECT product_id FROM products", binding)
        self.assertEqual(engine.artifacts.content(query["artifact_id"]), content)
        self.assertEqual(engine.database.inspect("products")["rows"], 2)

    def test_membership_and_annotations_survive_restart_and_reenrollment(self):
        engine = self.application()
        engine.change_tables(
            "enroll",
            name="extrafacts",
            description="自定义说明",
            column_meanings={"value": "事实数值"},
        )
        engine.change_tables("remove", name="products")
        restarted = self.application()
        self.assertNotIn("products", restarted.registry.tables)
        self.assertEqual(
            restarted.registry.tables["extrafacts"]["description"], "自定义说明"
        )
        restarted.change_tables("remove", name="extrafacts")
        restarted.change_tables("enroll", name="extrafacts")
        self.assertEqual(
            restarted.registry.tables["extrafacts"]["columns"]["value"]["meaning"],
            "事实数值",
        )

    def test_relationships_and_metrics_follow_table_visibility(self):
        engine = self.application()
        engine.change_tables("enroll", name="extrafacts")
        relation = {"from": "extrafacts.product_id", "to": "products.product_id"}
        self.assertIn(relation, engine.registry.relations)
        engine.registry.catalog["metrics"]["product_test"] = {
            "status": "published",
            "fields": ["products.product_id"],
        }
        self.assertIn("product_test", engine.registry.published("metrics"))
        engine.change_tables("remove", name="products")
        self.assertNotIn(relation, engine.registry.relations)
        self.assertNotIn("product_test", engine.registry.published("metrics"))
        engine.change_tables("enroll", name="products")
        self.assertIn(relation, engine.registry.relations)

    def test_refresh_updates_columns_and_detects_missing_physical_tables(self):
        engine = self.application()
        engine.change_tables("enroll", name="extrafacts")
        with self.source_database.connection() as db:
            db.execute("ALTER TABLE extrafacts ADD COLUMN note TEXT")
        engine.change_tables("refresh")
        self.assertIn("note", engine.registry.tables["extrafacts"]["columns"])
        with self.source_database.connection() as db:
            db.execute("DROP TABLE extrafacts")
        engine.change_tables("refresh")
        self.assertNotIn("extrafacts", engine.registry.tables)
        self.assertIn("extrafacts", engine.registry.unavailable_tables)

    def test_invalid_enrollment_is_atomic_and_version_conflicts_are_rejected(self):
        engine = self.application()
        version = engine.registry.catalog_version
        tables = deepcopy(engine.registry.tables)
        for options in [
            {"name": "Missing"},
            {"name": "extrafacts", "column_meanings": {"missing": "bad"}},
            {
                "name": "extrafacts",
                "relations": [{"from": "extrafacts.value", "to": "Missing.value"}],
            },
            {"name": "extrafacts", "expected_version": "stale"},
        ]:
            with self.assertRaises(ValueError):
                engine.change_tables("enroll", **options)
        self.assertEqual(engine.registry.catalog_version, version)
        self.assertEqual(engine.registry.tables, tables)
        restarted = self.application()
        self.assertNotIn("extrafacts", restarted.registry.tables)

    def test_catalog_updates_wait_for_idle_execution(self):
        engine = self.application()
        with engine.runtime.operation():
            with self.assertRaisesRegex(ValueError, "正在执行"):
                engine.change_tables("remove", name="products")
        self.assertIn("products", engine.registry.tables)

    def test_source_provider_reload_keeps_catalogs_separate(self):
        engine = self.application()
        from enterprise_agent.catalog.registry import Registry

        with patch.dict(
            "os.environ", {"DATABASE_URL": "postgresql://unused.invalid/unused"}
        ):
            self.assertEqual(
                Registry(engine.database, engine.stores.directory).backend, "postgres"
            )
        engine.change_tables("enroll", name="extrafacts")
        engine.change_tables("remove", name="products")
        alternate = namespace(self.path, "_alternate")
        alternate_database = Postgres(test_dsn(), alternate)
        alternate_database.setup(
            1,
            ["CREATE TABLE products(product_id INTEGER PRIMARY KEY,product_name TEXT)"],
        )
        engine.runtime.reload(
            "database.readonly", {"dsn": test_dsn(), "schema": alternate}
        )
        self.assertIn("products", engine.registry.tables)
        self.assertNotIn("extrafacts", engine.registry.tables)
        engine.runtime.reload(
            "database.readonly", {"dsn": test_dsn(), "schema": self.source}
        )
        self.assertNotIn("products", engine.registry.tables)
        self.assertIn("extrafacts", engine.registry.tables)

    def test_view_enrollment_and_source_identity_are_reflected_in_provenance(self):
        engine = self.application()
        with self.source_database.connection() as db:
            db.execute("CREATE VIEW productview AS SELECT product_id FROM products")
        engine.change_tables("enroll", name="productview")
        binding = {"status": "bound", "tables": ["productview"], "relations": []}
        _, rows, _ = engine.query_gateway.execute(
            "SELECT product_id FROM productview ORDER BY product_id", binding
        )
        self.assertEqual(rows, [{"product_id": 1}, {"product_id": 2}])
        first_id = engine.registry.source_id
        first_version = engine.registry.catalog_version
        alternate = namespace(self.path, "_same_shape")
        alternate_database = Postgres(test_dsn(), alternate)
        alternate_database.setup(
            1,
            [
                "CREATE TABLE products(product_id INTEGER PRIMARY KEY,product_name TEXT)",
                "CREATE TABLE extrafacts(fact_id INTEGER PRIMARY KEY,product_id INTEGER,value DOUBLE PRECISION)",
            ],
        )
        engine.runtime.reload(
            "database.readonly", {"dsn": test_dsn(), "schema": alternate}
        )
        self.assertNotEqual(engine.registry.source_id, first_id)
        self.assertNotEqual(engine.registry.catalog_version, first_version)

    def test_stale_catalog_writer_cannot_overwrite_other_enrollments(self):
        engine = self.application()
        other = self.application()
        engine.change_tables("enroll", name="extrafacts")
        with self.assertRaisesRegex(ValueError, "目录已被更新"):
            other.change_tables("remove", name="products")
        restarted = self.application()
        self.assertIn("products", restarted.registry.tables)
        self.assertIn("extrafacts", restarted.registry.tables)

    def test_admin_http_enrollment_and_removal(self):
        from fastapi.testclient import TestClient
        from enterprise_agent.server import app as adapter
        from enterprise_agent.server import deps, auth as identity

        engine = self.application()
        with (
            identity_scope(self.path),
            patch.object(deps, "application", lambda: engine),
        ):
            identity.create_user("catalog-admin", "catalog-admin-password", "admin")
            identity.create_user(
                "catalog-analyst", "catalog-analyst-password", "analyst"
            )
            with TestClient(adapter.app) as client:
                tokens = {}
                for role in ("admin", "analyst"):
                    response = client.post(
                        "/auth/login",
                        json={
                            "username": f"catalog-{role}",
                            "password": f"catalog-{role}-password",
                        },
                    )
                    tokens[role] = {
                        "Authorization": "Bearer " + response.json()["access_token"]
                    }
                self.assertEqual(
                    client.post(
                        "/catalog/tables",
                        json={"name": "extrafacts"},
                        headers=tokens["analyst"],
                    ).status_code,
                    403,
                )
                available = client.get(
                    "/catalog/tables/available", headers=tokens["admin"]
                )
                self.assertEqual(available.status_code, 200, available.text)
                enrolled = client.post(
                    "/catalog/tables",
                    json={"name": "extrafacts"},
                    headers=tokens["admin"],
                )
                self.assertEqual(enrolled.status_code, 200, enrolled.text)
                self.assertIn("extrafacts", enrolled.json()["tables"])
                removed = client.delete(
                    "/catalog/tables/extrafacts", headers=tokens["admin"]
                )
                self.assertEqual(removed.status_code, 200, removed.text)
                refreshed = client.post(
                    "/catalog/tables/refresh", json={}, headers=tokens["admin"]
                )
                self.assertEqual(refreshed.status_code, 200, refreshed.text)
                self.assertEqual(engine.database.inspect("extrafacts")["rows"], 2)
