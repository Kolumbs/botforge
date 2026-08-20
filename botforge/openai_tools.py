"""Adapter binding framework-neutral ToolSpec descriptors to the OpenAI Agents SDK.

This is the only module that converts botforge's tools into SDK objects. The
descriptors themselves live in ``tools.py`` and know nothing about any SDK, so
swapping agent frameworks means writing a sibling of this file rather than
touching the tool layer.
"""

import dataclasses

from agents import FunctionTool, RunContextWrapper

from .tools import ToolSpec


__all__ = ["ToolSpec", "to_function_tools"]


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
