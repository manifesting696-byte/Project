"""Fan out ready tasks, then collect the whole wave before dispatching again."""

from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from enterprise_agent.flow.state import ScheduleState
from enterprise_agent.stages.execute.task_nodes import collect, dispatch, execute


def send_ready(state):
    if not state["ready"]:
        return "dispatch" if state["retry_dispatch"] else END
    return [Send("execute_task", {**state, "task_id": key}) for key in state["ready"]]


def build(runner):
    graph = StateGraph(ScheduleState)
    graph.add_node("dispatch", partial(dispatch, runner))
    graph.add_node("execute_task", partial(execute, runner))
    graph.add_node("collect", collect)
    graph.add_edge(START, "dispatch")
    graph.add_conditional_edges("dispatch", send_ready)
    graph.add_edge("execute_task", "collect")
    graph.add_conditional_edges("collect", lambda state: state["route"])
    return graph
