"""Agent assembly from stored configuration and tools."""

import logging

from agents import Agent
from agents.extensions.models.litellm_model import LitellmModel

from .dynamic_tools import (
    PROVIDERS,
    ROOT_AGENT,
    build_bootstrap_tool_specs,
    build_dynamic_tool_specs,
    get_provider,
)
from .openai_tools import to_function_tools


log = logging.getLogger(__name__)


def resolve_model(provider):
    """Bind a model to the adapter."""
    return LitellmModel(
        model=f"{provider.name}/{provider.model}", api_key=provider.api_key
    )


def build_agent(memory, name=ROOT_AGENT, unconfigured_prompt=""):
    """Assemble an Agent from stored configuration and tools.

    Everything comes from the database, including which LLM to use - see
    ``setup``, which fills that in on first boot. Agents that name this one in
    ``exposed_to`` are built too and attached as tools, so the returned agent
    can delegate to them. Only the root agent carries the bootstrap tools.

    :param memory: a membank.LoadMemory (SQLite dataclass store).
    :param name: which agent to build; defaults to the one people talk to.
    :param unconfigured_prompt: what the root agent says before an
        administrator has given it a personality, from the config of the same
        name. Empty simply leaves it without instructions, as sub-agents are.
    :return: an agents.Agent ready to run.
    """
    provider = get_provider(memory)
    if not provider or not provider.api_key:
        raise RuntimeError("No LLM configured yet - run first-boot setup")
    if provider.name not in PROVIDERS:
        raise RuntimeError(
            f"Stored provider {provider.name!r} is not one the adapter can "
            f"reach; expected one of: {', '.join(PROVIDERS)}"
        )
    return _build(memory, name, provider, unconfigured_prompt, frozenset())


def _build(memory, name, provider, unconfigured, building):
    """Build one agent, recursing into whatever delegates from it."""
    config = memory.get.botconfig(name=name)

    instructions = unconfigured if name == ROOT_AGENT else ""
    if config:
        instructions = config.instructions or instructions

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
            _build(memory, sub.name, provider, unconfigured, building).as_tool(
                tool_name=sub.name,
                tool_description=sub.description or f"Delegate to the {sub.name} agent.",
            )
        )

    return Agent(
        name=name,
        instructions=instructions,
        model=resolve_model(provider),
        tools=tools,
    )
