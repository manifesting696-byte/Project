def render_chart(
    self, source_id, chart_type, x, series, title, horizontal=False, selector=None
):
    self.authorize(source_id)
    if chart_type not in ("bar", "line", "scatter"):
        raise ValueError("支持 bar/line/scatter 图表")
    source = self.inspect(source_id)
    if source["content_type"] != "table":
        raise ValueError("图表数据必须来自查询产物")
    columns = {col["name"] for col in source["payload"]["columns"]}
    if (
        x not in columns
        or not series
        or any(item.get("field") not in columns for item in series)
    ):
        raise ValueError("图表字段必须存在于真实数据")
    selection = self.artifacts.read(source_id, selector)
    if selection["has_more"] and not (selector or {}).get("limit"):
        raise ValueError("数据超过图表展示范围，请明确筛选或先聚合，不能把预览当全体")
    rows = selection["rows"]
    colors = ["#202020", "#666666", "#a0a0a0", "#454545"]
    plotted = []
    for index, item in enumerate(series):
        name, field = item.get("name", item["field"]), item["field"]
        color = item.get("color", colors[index % len(colors)])
        plotted.append(
            {
                "name": name,
                "type": chart_type,
                "encode": {
                    "x": field if horizontal else x,
                    "y": x if horizontal else field,
                },
                "itemStyle": {"color": color},
                "lineStyle": {"color": color},
            }
        )
    config = {
        "title": {"text": title},
        "legend": {"data": [item["name"] for item in plotted]},
        "tooltip": {"trigger": "axis"},
        "dataset": {"source": rows},
        "xAxis": {
            "type": "value" if horizontal or chart_type == "scatter" else "category"
        },
        "yAxis": {"type": "category" if horizontal else "value"},
        "series": plotted,
        "selection": {
            "source_id": source_id,
            "selector": selector or {},
            "total_rows": selection["total_rows"],
            "displayed_rows": len(rows),
        },
    }
    return self.save(
        "chart",
        title,
        title,
        config,
        [source_id],
        {"chart_type": chart_type, "title": title},
    )
