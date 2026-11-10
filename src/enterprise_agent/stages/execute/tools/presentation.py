from functools import partial

from enterprise_agent.model.tool_call import ARRAY, OBJECT, STRING, tool
from enterprise_agent.stages.execute.tools.chart import render_chart
from enterprise_agent.stages.execute.tools.report import render_report
from enterprise_agent.stages.execute.tools.table import compose_table


def presentation_tools(self):
    render_chart_call = partial(render_chart, self)
    render_report_call = partial(render_report, self)
    compose_table_call = partial(compose_table, self)
    return {
        "compose_table": tool(
            "制作用户要求的展示表，保留真实来源。数值运算交给 compute_artifact，不能编造数据",
            compose_table_call,
            title=STRING,
            summary=STRING,
            rows={"type": "array", "items": OBJECT},
            source_ids=ARRAY,
            columns=ARRAY,
        ),
        "render_chart": tool(
            "按真实查询列生成 ECharts 配置；保存数据、轴映射、系列名、颜色；支持横向图与多系列",
            render_chart_call,
            source_id=STRING,
            chart_type=STRING,
            x=STRING,
            series={"type": "array", "items": OBJECT},
            title=STRING,
            horizontal={"type": "boolean"},
            selector=OBJECT,
        ),
        "render_report": tool(
            "保存有实际数据/分析/图表来源引用的完整 Markdown 报告",
            render_report_call,
            title=STRING,
            body=STRING,
            source_ids=ARRAY,
        ),
    }
