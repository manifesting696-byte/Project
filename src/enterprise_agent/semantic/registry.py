"""Database metadata is separate from approved business definitions."""

import hashlib
import json
import os
import re
from copy import deepcopy
import yaml
from ..core.config import ROOT, WORK
from .catalog import TableCatalog


class Registry:
    def __init__(self, database=None, directory=None):
        profile = json.loads((WORK / "data_quality.json").read_text(encoding="utf-8"))
        self.snapshot_id = profile["snapshot_id"]
        version = os.getenv("SEMANTIC_CATALOG_VERSION")
        path = ROOT / "semantic.yaml"
        if version:
            if not re.fullmatch(r"[\w.-]+", version):
                raise ValueError("无效语义版本")
            path = ROOT / "semantic_versions" / f"{version}.yaml"
            expected = path.with_suffix(".sha256").read_text(encoding="ascii").strip()
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("语义目录校验不一致")
        self.catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
        if version and (
            self.catalog.get("status") != "published"
            or self.catalog.get("snapshot_id") != self.snapshot_id
        ):
            raise ValueError("发布目录或快照不匹配")
        meanings, table = {}, None
        for line in (
            (ROOT / "docs" / "电子供应链数据表说明.md")
            .read_text(encoding="utf-8")
            .splitlines()
        ):
            match = re.match(r"^##\s+\d+\.\s+(\w+)", line)
            if match:
                table = match[1]
            match = re.match(
                r"^\|\s*`([^`]+)`\s*\|\s*([^|]+)\|[^|]*\|\s*([^|]+)\|", line
            )
            if table and match:
                meanings[f"{table}.{match[1]}"] = (
                    f"{match[2].strip()}：{match[3].strip()}"
                )
        self.backend = (
            database.backend
            if database is not None
            else ("postgres" if os.getenv("DATABASE_URL") else "sqlite")
        )
        self.tables = {}
        for name, entry in profile["tables"].items():
            self.tables[name.lower() if self.backend == "postgres" else name] = {
                "rows": entry["rows"],
                "primary_key": entry["primary_key"],
                "columns": {
                    col: {
                        "type": entry["types"][col],
                        "meaning": meanings.get(f"{name}.{col}", ""),
                    }
                    for col in entry["columns"]
                },
            }
        self.relations = json.loads(
            (ROOT / "config" / "relations.json").read_text(encoding="utf-8")
        )
        if self.backend == "postgres":
            self.relations = [
                {key: value.lower() for key, value in item.items()}
                for item in self.relations
            ]
        self.database = database
        self.source_id = database.source_id if database is not None else None
        self._baseline = deepcopy(self.tables)
        self._base_relations = deepcopy(self.relations)
        self._catalog_store = (
            TableCatalog(directory, database.source_id)
            if database is not None and directory is not None
            else None
        )
        self.catalog_revision, self._entries = (
            self._catalog_store.read() if self._catalog_store else (0, {})
        )
        self.unavailable_tables = []
        if self._catalog_store:
            self.backend = database.backend
            self._apply(self._prepare(self._entries))
        else:
            self.catalog_version = self._version(self.tables, self.relations)

    def _version(self, tables, relations):
        return hashlib.sha256(
            json.dumps(
                {"source_id": self.source_id, "tables": tables, "relations": relations},
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()[:20]

    @staticmethod
    def _has_field(field, tables):
        if not isinstance(field, str) or "." not in field:
            return False
        table, column = field.rsplit(".", 1)
        found = next(
            (value for name, value in tables.items() if name.lower() == table.lower()),
            None,
        )
        return found is not None and column in found["columns"]

    def _prepare(self, entries):
        available = {item["name"] for item in self.database.available()}
        names = {name for name in self._baseline if name in available}
        missing = {name for name in self._baseline if name not in available}
        for name, entry in entries.items():
            if entry["enabled"] and name in available:
                names.add(name)
            elif entry["enabled"]:
                missing.add(name)
            else:
                names.discard(name)
                missing.discard(name)
        if len({name.lower() for name in names}) != len(names):
            raise ValueError("接入的表名忽略大小写后必须唯一")
        tables, relations = {}, list(self._base_relations)
        for name in sorted(names):
            entry = self.database.inspect(name)
            relations.extend(entry.pop("foreign_keys", []))
            options = entries.get(name, {})
            meanings = options.get("column_meanings", {})
            baseline = self._baseline.get(name, {}).get("columns", {})
            for column, definition in entry["columns"].items():
                definition["meaning"] = meanings.get(
                    column, baseline.get(column, {}).get("meaning", "")
                )
            entry["description"] = options.get("description", "")
            tables[name] = entry
            relations.extend(options.get("relations", []))
        valid_relations = []
        for relation in relations:
            if (
                all(
                    self._has_field(relation.get(key), tables) for key in ("from", "to")
                )
                and relation not in valid_relations
            ):
                valid_relations.append(relation)
        return tables, valid_relations, sorted(missing)

    def _apply(self, prepared):
        tables, relations, missing = prepared
        version = self._version(tables, relations)
        self.tables, self.relations = tables, relations
        self.unavailable_tables, self.catalog_version = missing, version

    def _managed(self):
        if self._catalog_store is None:
            raise ValueError("当前语义目录 Provider 未启用数据表接入管理")

    def available_tables(self):
        self._managed()
        return {
            "source_id": self.database.source_id,
            "catalog_version": self.catalog_version,
            "tables": [
                {**item, "registered": item["name"] in self.tables}
                for item in self.database.available()
            ],
        }

    def _commit(self, entries, expected_version=None):
        self._managed()
        if expected_version is not None and expected_version != self.catalog_version:
            raise ValueError("问析目录版本已变化，请刷新后重试")
        prepared = self._prepare(entries)
        revision = self._catalog_store.save(self.catalog_revision, entries)
        self._entries, self.catalog_revision = entries, revision
        self._apply(prepared)
        return self.overview()

    def enroll(
        self,
        name,
        description=None,
        column_meanings=None,
        relations=None,
        expected_version=None,
    ):
        self._managed()
        candidates = [
            item["name"]
            for item in self.database.available()
            if item["name"].lower() == name.lower()
        ]
        if len(candidates) != 1:
            raise ValueError("数据表不存在、不可读取或名称有歧义")
        name = candidates[0]
        metadata = self.database.inspect(name)
        if not set(column_meanings or {}) <= metadata["columns"].keys():
            raise ValueError("字段说明引用了不存在的字段")
        entries = deepcopy(self._entries)
        previous = entries.get(name, {})
        entries[name] = {
            "enabled": True,
            "description": description
            if description is not None
            else previous.get("description", ""),
            "column_meanings": dict(column_meanings)
            if column_meanings is not None
            else previous.get("column_meanings", {}),
            "relations": list(relations)
            if relations is not None
            else previous.get("relations", []),
        }
        prepared_tables = self._prepare(entries)[0]
        for relation in relations or []:
            if set(relation) != {"from", "to"} or not all(
                self._has_field(relation[key], prepared_tables)
                for key in ("from", "to")
            ):
                raise ValueError("关系必须引用已接入表中的真实字段")
        return self._commit(entries, expected_version)

    def remove(self, name, expected_version=None):
        self._managed()
        candidates = [
            table
            for table in set(self.tables) | set(self._entries) | set(self._baseline)
            if table.lower() == name.lower()
        ]
        if len(candidates) != 1:
            raise ValueError("表尚未接入或名称有歧义")
        entries = deepcopy(self._entries)
        entries[candidates[0]] = {**entries.get(candidates[0], {}), "enabled": False}
        return self._commit(entries, expected_version)

    def refresh(self, expected_version=None):
        return self._commit(deepcopy(self._entries), expected_version)

    def overview(self):
        return {
            "backend": self.backend,
            "source_id": self.source_id,
            "snapshot_id": self.snapshot_id,
            "catalog_version": self.catalog_version,
            "catalog_revision": self.catalog_revision,
            "unavailable_tables": list(self.unavailable_tables),
            "semantic_version": self.catalog["version"],
            "tables": self.tables,
            "relations": self.relations,
            "metrics": self.published("metrics"),
            "unapproved_metrics": [
                {"id": key, "name": value.get("name"), "status": value.get("status")}
                for key, value in self.catalog.get("metrics", {}).items()
                if value.get("status") != "published"
            ],
            "limitations": self.catalog.get("limitations", []),
        }

    def published(self, section):
        return {
            key: value
            for key, value in self.catalog.get(section, {}).items()
            if value.get("status") == "published"
            and all(
                self._has_field(field, self.tables) for field in value.get("fields", [])
            )
        }

    def search(self, query=""):
        data = self.overview()
        if not query:
            return data
        words = query.lower().split()
        matches = lambda value: any(
            word in json.dumps(value, ensure_ascii=False).lower() for word in words
        )
        return {
            **data,
            "tables": {
                key: value
                for key, value in data["tables"].items()
                if matches({key: value})
            },
            "metrics": {
                key: value
                for key, value in data["metrics"].items()
                if matches({key: value})
            },
        }

    def metric(self, metric_id):
        item = self.published("metrics").get(metric_id)
        if item is None:
            raise ValueError("该指标尚无已发布定义，不能自行补造口径")
        return item

    def validate_binding(self, binding):
        if binding.status != "bound":
            return
        if not set(binding.metrics) <= self.published("metrics").keys():
            raise ValueError("绑定包含未发布指标")
        tables = {name.lower(): value for name, value in self.tables.items()}
        if not {name.lower() for name in binding.tables} <= tables.keys():
            raise ValueError("绑定引用不存在的数据表")
        for field in binding.fields:
            table, column = field.rsplit(".", 1)
            if (
                table.lower() not in tables
                or column not in tables[table.lower()]["columns"]
            ):
                raise ValueError("绑定引用不存在的字段")
        if any(
            dimension not in self.published("dimension_ids")
            and dimension not in binding.fields
            for dimension in binding.dimensions
        ):
            raise ValueError("业务维度需使用已发布定义，原始分组需明确绑定字段")
        if any(item not in self.relations for item in binding.relations):
            raise ValueError("绑定关系未登记")
