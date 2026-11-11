"""One turn: intent → prepare → commit → unified Task execution → respond."""

from functools import partial

from langgraph.graph import END, START, StateGraph

from enterprise_agent.flow.state import TurnState
from enterprise_agent.stages.answer.graph_node import respond
from enterprise_agent.stages.execute.graph_node import process
from enterprise_agent.stages.intent.graph_node import intent
from enterprise_agent.stages.prepare.commit import commit
from enterprise_agent.stages.prepare.graph_node import prepare


def next_operation(state):
    # Cursor retains old checkpoint compatibility; process now handles the entire
    # remaining turn through the shared Task scheduler, not one private DAG each.
    return "process" if state["cursor"] < len(state["ordered"]) else "respond"


def build(runner):
    graph = StateGraph(TurnState)
    for name, node in (
        ("intent", intent),
        ("prepare", prepare),
        ("commit", commit),
        ("process", process),
        ("respond", respond),
    ):
        graph.add_node(name, partial(node, runner))
    graph.add_edge(START, "intent")
    graph.add_conditional_edges(
        "intent", lambda s: "respond" if s.get("error") else "prepare"
    )
    graph.add_edge("prepare", "commit")
    graph.add_conditional_edges("commit", next_operation)
    graph.add_conditional_edges("process", next_operation)
    graph.add_edge("respond", END)
    return graph
