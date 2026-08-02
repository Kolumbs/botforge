"""Agent assembly from stored configuration and tools."""

import logging

from agents import Agent

from .dynamic_tools import (
    PROVIDERS,
    ROOT_AGENT,
    build_bootstrap_tool_specs,
    build_dynamic_tool_specs,
    get_provider,
)
from .openai_tools import to_function_tools


log = logging.getLogger(__name__)


# Shown until an administrator gives this bot a personality with
# set_instructions. Setup has already run by the time an agent exists, so the
# administrator and the LLM are in place and only the character is missing.
UNCONFIGURED_PROMPT = (
    "You are a bot on the kolumbs.net platform that has not been given a "
    "character yet.\n\n"
    "Say so plainly, and tell the administrator they can:\n"
    "- give you a personality with **set_instructions(text)**\n"
    "- teach you a capability with **define_tool(name, description, source_code)**\n"
    "- create a specialist to delegate to with **define_agent(...)**\n"
    "- change the model or key with **set_provider(...)**\n\n"
    "Call **list_tools()** or **list_agents()** to show what already exists. "
    "Only the administrator can change any of this."
)

def resolve_model(provider, model):
    """Return something an Agent can use as its model.

    One provider is the SDK's native path and takes a plain model name. The
    rest go through LiteLLM, which the SDK ships as an optional extra and which
    is handed the stored key directly.
    """
    prefix = PROVIDERS.get(provider.name, {}).get("litellm_prefix", provider.name)
    if not prefix:
        return model
    try:
        from agents.extensions.models.litellm_model import LitellmModel
    except ImportError:
        raise RuntimeError(
            f"Provider {provider.name!r} needs LiteLLM. "
            "Install it with: pip install 'botforge[litellm]'"
        ) from None
    return LitellmModel(model=f"{prefix}/{model}", api_key=provider.api_key)


def build_agent(memory, name=ROOT_AGENT):
    """Assemble an Agent from stored configuration and tools.

    Everything comes from the database, including which LLM to use - see
    ``setup``, which fills that in on first boot. Agents that name this one in
    ``exposed_to`` are built too and attached as tools, so the returned agent
    can delegate to them. Only the root agent carries the bootstrap tools.

    :param memory: a membank.LoadMemory (SQLite dataclass store).
    :param name: which agent to build; defaults to the one people talk to.
    :return: an agents.Agent ready to run.
    """
    provider = get_provider(memory)
    if not provider or not provider.api_key:
        raise RuntimeError("No LLM configured yet - run first-boot setup")
    return _build(memory, name, provider, frozenset())


def _build(memory, name, provider, building):
    """Build one agent, recursing into whatever delegates from it."""
    config = memory.get.botconfig(name=name)

    instructions = UNCONFIGURED_PROMPT if name == ROOT_AGENT else ""
    model = provider.model
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
            _build(memory, sub.name, provider, building).as_tool(
                tool_name=sub.name,
                tool_description=sub.description or f"Delegate to the {sub.name} agent.",
            )
        )

    return Agent(
        name=name,
        instructions=instructions,
        model=resolve_model(provider, model),
        tools=tools,
    )
