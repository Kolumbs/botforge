"""Agent assembly from stored configuration and tools."""

import os

from agents import Agent, set_default_openai_key

from .dynamic_tools import build_bootstrap_tool_specs, build_dynamic_tool_specs
from .openai_tools import to_function_tools


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


def configure_provider(conf):
    """Apply LLM credentials. Called once at startup, not per turn.

    ``api_key`` configures the SDK's native OpenAI path. Any other provider is
    reached through LiteLLM, which takes its credentials from the environment,
    so ``[botforge.env]`` entries are exported here.
    """
    api_key = conf.get("api_key")
    env = conf.get("env") or {}
    if not api_key and not env:
        raise RuntimeError(
            "botforge needs LLM credentials: set 'api_key' for OpenAI, or "
            "[botforge.env] entries for another provider"
        )
    if api_key:
        set_default_openai_key(api_key)
    for name, value in env.items():
        os.environ[name] = str(value)


def resolve_model(name):
    """Return something an Agent can use as its model.

    A bare name (``gpt-4o-mini``) uses the SDK's native OpenAI path. A
    provider-qualified name (``anthropic/claude-opus-5``, ``gemini/...``) goes
    through LiteLLM, which the SDK ships as an optional extra.
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
    return LitellmModel(model=name)


def build_agent(memory, default_model=DEFAULT_MODEL):
    """Assemble an Agent from stored configuration and tools.

    Credentials are not handled here - see ``configure_provider``, which runs
    once at startup rather than on every turn.

    :param memory: a membank.LoadMemory (SQLite dataclass store).
    :param default_model: model to use when the stored config names none.
    :return: an agents.Agent ready to run.
    """
    instructions = UNCONFIGURED_PROMPT
    model = default_model

    bot_config = memory.get.botconfig(id=1)
    if bot_config:
        if bot_config.instructions:
            instructions = bot_config.instructions
        if bot_config.model:
            model = bot_config.model

    specs = build_bootstrap_tool_specs() + build_dynamic_tool_specs(memory)
    context = {"memory": memory, "talker": ""}  # talker is filled in per call

    return Agent(
        name="botforge",
        instructions=instructions,
        model=resolve_model(model),
        tools=to_function_tools(specs, context),
    )
