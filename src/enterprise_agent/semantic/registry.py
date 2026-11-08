"""Database metadata is separate from approved business definitions."""
import hashlib
import json
import os
import re
import yaml
from ..core.config import ROOT, WORK


class Registry:
    def __init__(self):
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
        if version and (self.catalog.get("status") != "published" or self.catalog.get("snapshot_id") != self.snapshot_id):
            raise ValueError("发布目录或快照不匹配")
        meanings, table = {}, None
        for line in (ROOT / "docs" / "电子供应链数据表说明.md").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^##\s+\d+\.\s+(\w+)", line)
            if match:
                table = match[1]
            match = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+)\|[^|]*\|\s*([^|]+)\|", line)
            if table and match:
                meanings[f"{table}.{match[1]}"] = f"{match[2].strip()}：{match[3].strip()}"
        self.backend = "postgres" if os.getenv("DATABASE_URL") else "sqlite"
        self.tables = {}
        for name, entry in profile["tables"].items():
            self.tables[name.lower() if self.backend == "postgres" else name] = {
                "rows": entry["rows"], "primary_key": entry["primary_key"],
                "columns": {col: {"type": entry["types"][col], "meaning": meanings.get(f"{name}.{col}", "")}
                            for col in entry["columns"]}}
        self.relations = json.loads((ROOT / "config" / "relations.json").read_text(encoding="utf-8"))
        if self.backend == "postgres":
            self.relations = [{key: value.lower() for key, value in item.items()} for item in self.relations]

    def overview(self):
        return {"backend": self.backend, "snapshot_id": self.snapshot_id,
                "semantic_version": self.catalog["version"], "tables": self.tables,
                "relations": self.relations, "metrics": self.published("metrics"),
                "unapproved_metrics": [{"id": key, "name": value.get("name"), "status": value.get("status")}
                                       for key, value in self.catalog.get("metrics", {}).items() if value.get("status") != "published"],
                "limitations": self.catalog.get("limitations", [])}

    def published(self, section):
        return {key: value for key, value in self.catalog.get(section, {}).items() if value.get("status") == "published"}

    def search(self, query=""):
        data = self.overview()
        if not query:
            return data
        words = query.lower().split()
        matches = lambda value: any(word in json.dumps(value, ensure_ascii=False).lower() for word in words)
        return {**data, "tables": {key: value for key, value in data["tables"].items() if matches({key: value})},
                "metrics": {key: value for key, value in data["metrics"].items() if matches({key: value})}}

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
            if table.lower() not in tables or column not in tables[table.lower()]["columns"]:
                raise ValueError("绑定引用不存在的字段")
        if any(dimension not in self.published("dimension_ids") and dimension not in binding.fields for dimension in binding.dimensions):
            raise ValueError("业务维度需使用已发布定义，原始分组需明确绑定字段")
        if any(item not in self.relations for item in binding.relations):
            raise ValueError("绑定关系未登记")
