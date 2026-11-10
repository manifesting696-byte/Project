from enterprise_agent.model.tool_call import ARRAY, OBJECT, STRING, tool


def analysis_tools(self):
    def compute(artifact_ids, sql, title, summary):
        for key in artifact_ids:
            self.authorize(key)
        rows, columns = self.artifacts.compute(artifact_ids, sql, with_columns=True)
        result = self.save(
            "table",
            title,
            summary,
            rows,
            artifact_ids,
            {
                "columns": columns,
                "computation_sql": sql,
                "provenance": {"method": "artifact_compute"},
            },
            sql,
        )
        result["data"] = self.artifacts.read(result["artifact_id"], {"limit": 30})
        return result

    def save_analysis(
        title,
        summary,
        body,
        source_ids,
        findings=None,
        conclusion=None,
        next_steps=None,
    ):
        for finding in findings or []:
            for ref in finding.get("refs", []):
                if ref["kind"] != "artifact":
                    raise ValueError("分析证据需引用输入产物")
                self.authorize(ref["id"])
        payload = {"findings": findings or []}
        if conclusion:
            payload["conclusion"] = conclusion
        if next_steps:
            payload["next_steps"] = next_steps
        return self.save("text", title, summary, body, source_ids, payload)

    return {
        "compute_artifact": tool(
            "对完整输入数据执行精确 PostgreSQL SELECT；表名为 result_xxx，不连接业务库；派生数据另存产物",
            compute,
            artifact_ids=ARRAY,
            sql=STRING,
            title=STRING,
            summary=STRING,
        ),
        "save_artifact": tool(
            "保存完整分析正文、轻量发现和实际来源，返回 text Artifact 引用",
            save_analysis,
            title=STRING,
            summary=STRING,
            body=STRING,
            source_ids=ARRAY,
            findings={"type": "array", "items": OBJECT},
            conclusion=STRING,
            next_steps=ARRAY,
        ),
    }
