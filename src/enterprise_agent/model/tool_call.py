"""Tool schema and callable contracts used by registries and model invocations."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Tool:
    description: str
    parameters: dict
    call: Callable


def tool(description, call, **properties):
    import inspect

    required = [
        name
        for name, parameter in inspect.signature(call).parameters.items()
        if parameter.default is inspect.Parameter.empty and name in properties
    ]
    return Tool(
        description,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        call,
    )


STRING = {"type": "string"}


OBJECT = {"type": "object"}


ARRAY = {"type": "array", "items": STRING}
