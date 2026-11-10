"""Checkpointed request execution; node names are persisted contracts."""

from functools import partial

from langgraph.graph import END, START, StateGraph

from enterprise_agent.flow.state import ExecutionContext, ExecutionState
from enterprise_agent.stages.answer.graph_node import answer, waiting_answer
from enterprise_agent.stages.execute.request_nodes import begin, execute, wait
from enterprise_agent.stages.prepare.replan import replan
from enterprise_agent.stages.verify.graph_node import check, finalize


def build(runner):
    graph = StateGraph(ExecutionState, context_schema=ExecutionContext)
    for name, node in (
        ("begin", begin),
        ("execute", execute),
        ("check", check),
        ("replan", replan),
        ("finalize", finalize),
        ("answer", answer),
        ("waiting_answer", waiting_answer),
        ("wait", wait),
    ):
        graph.add_node(name, partial(node, runner))
    graph.add_edge(START, "begin")
    for name in ("begin", "execute", "check", "replan", "finalize", "wait"):
        graph.add_conditional_edges(name, lambda state: state["route"])
    graph.add_edge("waiting_answer", "wait")
    graph.add_edge("answer", END)
    return graph
