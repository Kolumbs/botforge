"""Agent assembly from stored configuration and tools."""

from agents import Agent, set_default_openai_key

from .dynamic_tools import build_bootstrap_tool_specs, build_dynamic_tool_specs
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

    :param conf: the ``[botforge]`` config section (for API key, model override).
    :param memory: a membank.LoadMemory (SQLite dataclass store).
    :param model: optional override for the model. Falls back to conf, then BotConfig, then default.
    :return: an agents.Agent ready to run.
    """
    try:
        api_key = conf["api_key"]
    except KeyError:
        raise RuntimeError("botforge requires an 'api_key' in config [botforge] section") from None

    set_default_openai_key(api_key)

    instructions = UNCONFIGURED_PROMPT
    actual_model = model or conf.get("model", "gpt-4o-mini")

    bot_config = memory.get.botconfig(id=1)
    if bot_config:
        if bot_config.instructions:
            instructions = bot_config.instructions
        if bot_config.model:
            actual_model = bot_config.model

    specs = build_bootstrap_tool_specs() + build_dynamic_tool_specs(memory)
    context = {"memory": memory, "talker": ""}  # talker is filled in per call

    return Agent(
        name="botforge",
        instructions=instructions,
        model=actual_model,
        tools=to_function_tools(specs, context),
    )
