"""Agent assembly from stored configuration and tools."""

import logging

from agents import Agent

from .dynamic_tools import (
    ROOT_AGENT,
    build_bootstrap_tool_specs,
    build_dynamic_tool_specs,
)
from .openai_tools import to_function_tools


log = logging.getLogger(__name__)


UNCONFIGURED_PROMPT = (
    "You are an unconfigured bot on the kolumbs.net platform.\n\n"
    "To get started:\n"
    "1. If nobody has claimed admin yet, call **claim_admin()** to become the admin.\n"
    "2. Once you're admin, call **set_instructions(text)** to give the bot a personality.\n"
    "3. Then call **define_tool(name, description, source_code)** to teach the bot new capabilities.\n\n"
    "For more, call **list_tools()** to see all available tools.\n"
    "Remember: only the admin can modify the bot's behavior."
)

DEFAULT_MODEL = "gpt-4o-mini"


def resolve_model(name, api_key):
    """Return something an Agent can use as its model.

    A bare name (``gpt-4o-mini``) uses the SDK's OpenAI path, which reads the
    key configured at startup. A provider-qualified name
    (``anthropic/claude-opus-5``, ``gemini/...``) goes through LiteLLM, which
    the SDK ships as an optional extra and which is handed the same key.
    """
    if "/" not in name:
        return name
    try:
        from agents.extensions.models.litellm_model import LitellmModel
    except ImportError:
        raise RuntimeError(
            f"Model {name!r} names a provider, which needs LiteLLM. "
            "Install it with: pip install 'botforge[litellm]'"
        ) from None
    return LitellmModel(model=name, api_key=api_key)


def build_agent(memory, conf, name=ROOT_AGENT):
    """Assemble an Agent from stored configuration and tools.

    Agents that name this one in ``exposed_to`` are built too and attached as
    tools, so the returned agent can delegate to them. Only the root agent
    carries the bootstrap tools; a specialist gets just its own.

    :param memory: a membank.LoadMemory (SQLite dataclass store).
    :param conf: the ``[botforge]`` config section, for ``api_key`` and the
        default ``model``.
    :param name: which agent to build; defaults to the one people talk to.
    :return: an agents.Agent ready to run.
    """
    return _build(memory, name, conf, frozenset())


def _build(memory, name, conf, building):
    """Build one agent, recursing into whatever delegates from it."""
    config = memory.get.botconfig(name=name)

    instructions = UNCONFIGURED_PROMPT if name == ROOT_AGENT else ""
    model = conf.get("model", DEFAULT_MODEL)
    if config:
        instructions = config.instructions or config.description or instructions
        model = config.model or model

    specs = build_dynamic_tool_specs(memory, agent=name)
    if name == ROOT_AGENT:
        specs = build_bootstrap_tool_specs() + specs

    context = {"memory": memory, "talker": ""}  # talker is filled in per call
    tools = to_function_tools(specs, context)

    building = building | {name}
    for sub in memory.get("botconfig"):
        if sub.exposed_to != name:
            continue
        if sub.name in building:
            log.warning(
                "Not delegating from %r to %r: that would loop back on itself.",
                name,
                sub.name,
            )
            continue
        tools.append(
            _build(memory, sub.name, conf, building).as_tool(
                tool_name=sub.name,
                tool_description=sub.description or f"Delegate to the {sub.name} agent.",
            )
        )

    return Agent(
        name=name,
        instructions=instructions,
        model=resolve_model(model, conf.get("api_key")),
        tools=tools,
    )
