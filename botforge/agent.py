"""Agent assembly and bootstrap tool registration."""

from agents import Agent, set_default_openai_key

from .dynamic_tools import (
    BotConfig,
    _build_bootstrap_tool_specs,
    build_dynamic_function_tools,
)
from .openai_tools import to_function_tools


UNCONFIGURED_PROMPT = (
    "You are an unconfigured bot on the botforge platform.\n\n"
    "To get started:\n"
    "1. If nobody has claimed admin yet, call **claim_admin()** to become the admin.\n"
    "2. Once you're admin, call **set_instructions(text)** to give the bot a personality.\n"
    "3. Then call **define_tool(name, description, source_code)** to teach the bot new capabilities.\n\n"
    "For more, call **list_tools()** to see all available tools.\n"
    "Remember: only the admin can modify the bot's behavior."
)


def build_agent(conf, memory, model=None):
    """Build an OpenAI Agents SDK Agent, loading config and tools from the database.

    :param conf: the zoozl config dict (for API key, model override).
    :param memory: a membank.LoadMemory (SQLite dataclass store).
    :param model: optional override for the model. Falls back to conf, then BotConfig, then default.
    :return: an agents.Agent ready to run.
    """
    # Configure the OpenAI API key
    try:
        api_key = conf["botforge"]["api_key"]
    except KeyError:
        raise RuntimeError("botforge requires an 'api_key' in config [botforge] section") from None

    set_default_openai_key(api_key)

    # Load the bot's instructions and model from the database
    instructions = UNCONFIGURED_PROMPT
    actual_model = model or conf.get("botforge", {}).get("model", "gpt-4o-mini")

    try:
        bot_config = memory.get.bot_config(id=1)
        if bot_config:
            if bot_config.instructions:
                instructions = bot_config.instructions
            if bot_config.model:
                actual_model = bot_config.model
    except Exception:
        pass  # Use defaults if BotConfig doesn't exist yet

    # Build the bootstrap tools
    bootstrap_specs = _build_bootstrap_tool_specs()
    bootstrap_context = {"memory": memory, "talker": ""}
    bootstrap_tools = to_function_tools(bootstrap_specs, bootstrap_context)

    # Load dynamic tools from the database
    dynamic_tools = build_dynamic_function_tools(memory)

    # Combine all tools
    all_tools = bootstrap_tools + dynamic_tools

    # Create and return the agent
    return Agent(
        name="botforge",
        instructions=instructions,
        model=actual_model,
        tools=all_tools,
    )
