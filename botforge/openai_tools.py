"""Adapter binding framework-neutral ToolSpec descriptors to the OpenAI Agents SDK.

This module is the only place that imports the OpenAI Agents SDK to expose tool
definitions. The rest of the codebase stays SDK-agnostic; ToolSpec is a pure dataclass.
"""

import dataclasses

from agents import FunctionTool, RunContextWrapper


class ToolSpec:
    """Framework-neutral tool definition (like a blueprint for a function tool)."""

    def __init__(self, name, description, params, handler):
        self.name = name
        self.description = description
        self.params = params  # a pydantic BaseModel class
        self.handler = handler  # an async callable


def to_function_tools(specs, context):
    """Convert neutral ToolSpec descriptors into ``agents.FunctionTool`` objects.

    :param specs: iterable of ToolSpec (name, description, params model, handler).
    :param context: the base context object (e.g., an empty dict or a dataclass).
        Per call, the caller's ``talker`` is read from the agent run context (the
        zoozl Package passed to Runner.run) and merged into a copy of this context,
        so tools can resolve the current session without the LLM supplying an ID.
    """
    return [
        FunctionTool(
            name=spec.name,
            description=spec.description,
            params_json_schema=spec.params.model_json_schema(),
            on_invoke_tool=_make_invoker(spec, context),
            strict_json_schema=False,
        )
        for spec in specs
    ]


def _make_invoker(spec, context):
    """Build the ``on_invoke_tool`` coroutine for a single ToolSpec."""

    async def on_invoke(run_ctx: RunContextWrapper, args_json: str) -> str:
        import inspect
        params = spec.params.model_validate_json(args_json or "{}")
        talker = getattr(getattr(run_ctx, "context", None), "talker", "") or ""
        if isinstance(context, dict):
            ctx = {**context, "talker": talker}
        else:
            ctx = dataclasses.replace(context, talker=talker)
        result = spec.handler(ctx, params)
        if inspect.iscoroutine(result):
            return await result
        else:
            return result

    return on_invoke
