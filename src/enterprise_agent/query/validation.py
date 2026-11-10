"""Deterministic SQL validation for business queries and isolated Artifact computations."""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify


SAFE_ANONYMOUS = {
    "date",
    "round",
    "date_trunc",
    "date_part",
    "to_char",
    "to_date",
    "greatest",
    "least",
    "percentile_cont",
    "percentile_disc",
}


def validate_readonly(sql, schema, dialect="postgres", relations=None):
    try:
        expressions = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise ValueError("SQL 无法解析：" + str(exc)) from exc
    if len(expressions) != 1 or not isinstance(expressions[0], (exp.Select, exp.Union)):
        raise ValueError("只允许一条 SELECT 查询")
    tree = expressions[0]
    forbidden = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Command,
        exp.Into,
    )
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise ValueError("查询包含非只读操作")
    if any(
        fn.name.lower() not in SAFE_ANONYMOUS for fn in tree.find_all(exp.Anonymous)
    ):
        raise ValueError("查询包含未授权函数")
    ctes = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    known = {name.lower(): columns for name, columns in schema.items()}
    for table in tree.find_all(exp.Table):
        if table.catalog or (table.db and table.db.lower() != "analysis"):
            raise ValueError("查询包含未授权 Schema")
        if table.name.lower() not in known and table.name.lower() not in ctes:
            raise ValueError("查询包含未授权表")
        # Schema qualification is enforced by the database search_path; qualification
        # below uses one consistent level of names for PostgreSQL.
        table.set("db", None)
    normalized = {
        name.lower(): {col: "UNKNOWN" for col in columns}
        for name, columns in schema.items()
    }
    try:
        qualified = qualify(
            tree,
            dialect=dialect,
            schema=normalized,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except sqlglot.errors.SqlglotError as exc:
        raise ValueError("查询字段或别名不能由元数据验证：" + str(exc)) from exc
    if relations is not None:
        allowed = {
            frozenset((item["from"].lower(), item["to"].lower())) for item in relations
        }
        from sqlglot.optimizer.scope import traverse_scope

        for scope in traverse_scope(qualified):
            aliases = {
                key.lower(): value.name.lower()
                for key, value in scope.sources.items()
                if isinstance(value, exp.Table)
            }
            multiplied = set()
            for join in scope.expression.args.get("joins", []):
                if join.args.get("method") == "NATURAL" or not join.args.get("on"):
                    raise ValueError("关联查询必须明确 ON 条件")
                matched = False
                for eq in join.args["on"].find_all(exp.EQ):
                    left, right = eq.left, eq.right
                    if (
                        isinstance(left, exp.Column)
                        and isinstance(right, exp.Column)
                        and left.table.lower() in aliases
                        and right.table.lower() in aliases
                    ):
                        if left.table.lower() != right.table.lower():
                            pair = frozenset(
                                (
                                    f"{aliases[left.table.lower()]}.{left.name}".lower(),
                                    f"{aliases[right.table.lower()]}.{right.name}".lower(),
                                )
                            )
                            if pair not in allowed:
                                raise ValueError("JOIN 使用未登记关系")
                            matched = True
                            for relation in relations:
                                if (
                                    relation["to"].lower()
                                    == f"{aliases[left.table.lower()]}.{left.name}".lower()
                                ):
                                    multiplied.add(left.table.lower())
                                if (
                                    relation["to"].lower()
                                    == f"{aliases[right.table.lower()]}.{right.name}".lower()
                                ):
                                    multiplied.add(right.table.lower())
                if (
                    isinstance(join.this, exp.Table)
                    and join.this.alias_or_name.lower() in aliases
                    and not matched
                ):
                    # A join to a derived scope can be checked through its projected columns;
                    # two physical-table sides must have a registered relationship.
                    on_aliases = {
                        column.table.lower()
                        for column in join.args["on"].find_all(exp.Column)
                    }
                    if on_aliases <= aliases.keys():
                        raise ValueError("物理表之间必须使用登记关系关联")
            for aggregate in scope.expression.find_all(exp.AggFunc):
                if aggregate.find_ancestor(exp.Select) is not scope.expression:
                    continue
                if isinstance(aggregate.this, exp.Distinct):
                    continue
                columns = list(aggregate.find_all(exp.Column))
                if any(column.table.lower() in multiplied for column in columns):
                    raise ValueError(
                        "父表聚合会被明细关联重复计数，请按事实粒度预聚合后再关联"
                    )
    return qualified.sql(dialect=dialect)
