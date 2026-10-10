"""Dynamic tool definitions, admin grants, and the bot configuration.

Persisted in botforge's own membank store, opened by the plugin - not in
zoozl's root.memory, which holds zoozl's conversation-routing state.
"""

import dataclasses
import inspect
import logging
from datetime import datetime, timezone

import pydantic

from .tools import ToolSpec


log = logging.getLogger(__name__)


# Stored tool source is written against this contract, so changing its shape
# means bumping the version and migrating or re-authoring the stored rows.
CONTRACT_VERSION = 1

TOOL_CONTRACT = """\
A tool's source code must define exactly two names:

    import pydantic

    class Params(pydantic.BaseModel):
        ...                          # the tool's arguments

    async def handler(ctx, params) -> str:
        ...                          # ctx is a dict with "memory" and "talker"

`handler` must be declared with `async def` and take exactly two positional
arguments. Whatever it returns is handed back to the model as the tool result,
so return a string.
"""


# The agent a person talks to. Every other agent is reached by delegation from
# it, so this name is the entry point rather than just a default.
ROOT_AGENT = "main"

# The providers the adapter can reach, and the model each starts on. The name is
# LiteLLM's own, so it doubles as the prefix handed to the adapter and there is
# no second naming scheme to keep in step. The model is only a starting point;
# an administrator can change it with set_provider.
PROVIDERS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.0-flash",
}


@dataclasses.dataclass
class BotConfig:
    """One agent: its personality and who may delegate to it."""

    name: str = dataclasses.field(default=ROOT_AGENT, metadata={"key": True})
    instructions: str = ""
    model: str = ""  # provider/model of its own; empty follows the default provider
    description: str = ""  # shown to the agent that calls this one as a tool
    exposed_to: str = ""   # agent that may delegate here; empty means nobody
    updated_at: str = ""


@dataclasses.dataclass
class Provider:
    """An LLM provider and its key. Filled in by first-boot setup, not by config.

    The default provider runs every agent that has no model of its own.
    """

    name: str = dataclasses.field(default=None, metadata={"key": True})  # one of PROVIDERS
    api_key: str = ""
    model: str = ""  # what agents on this provider run unless they name a model
    default: bool = False
    updated_at: str = ""


@dataclasses.dataclass
class AdminGrant:
    """A session that has been granted admin privileges."""

    talker: str = dataclasses.field(default=None, metadata={"key": True})
    granted_at: str = ""


@dataclasses.dataclass
class DynamicTool:
    """A tool definition stored in the database."""

    name: str = dataclasses.field(default=None, metadata={"key": True})
    agent: str = ROOT_AGENT
    description: str = ""
    source_code: str = ""  # written against the contract in TOOL_CONTRACT
    enabled: bool = True
    created_by: str = ""
    updated_at: str = ""
    contract_version: int = CONTRACT_VERSION


class AdminAuthBase(pydantic.BaseModel):
    """Base params for admin-gated operations."""

    pass  # No secret field — admin-ness is session/talker-keyed, not secret-based.


class GrantAdminParams(AdminAuthBase):
    """Parameters for grant_admin."""

    talker: str = pydantic.Field(description="The session ID (talker) to grant admin to.")


class SetInstructionsParams(AdminAuthBase):
    """Parameters for set_instructions."""

    text: str = pydantic.Field(description="The agent's new system instructions/personality.")
    agent: str = pydantic.Field(
        default=ROOT_AGENT,
        description=f"Which agent to configure. Defaults to '{ROOT_AGENT}', the one people talk to.",
    )


class SetProviderParams(AdminAuthBase):
    """Parameters for set_provider."""

    provider: str = pydantic.Field(
        default="",
        description="Provider name. Empty means the default provider.",
    )
    api_key: str = pydantic.Field(
        default="",
        description="API key for that provider. Required the first time a provider is added.",
    )
    model: str = pydantic.Field(
        default="",
        description="Model agents on that provider run, e.g. gpt-4o-mini. Empty keeps the current one.",
    )
    default: bool = pydantic.Field(
        default=False,
        description="Make this the provider every agent without a model of its own runs on.",
    )


class SetModelParams(AdminAuthBase):
    """Parameters for set_model."""

    model: str = pydantic.Field(
        default="",
        description=(
            "provider/model, e.g. anthropic/claude-opus-5-5. A bare model name means "
            "the default provider. Empty puts the agent back on the default provider."
        ),
    )
    agent: str = pydantic.Field(
        default=ROOT_AGENT,
        description=f"Which agent runs on it. Defaults to '{ROOT_AGENT}', the one people talk to.",
    )


class ProviderNameParams(AdminAuthBase):
    """Parameters for delete_provider."""

    name: str = pydantic.Field(description="The provider whose key to forget.")


class DefineAgentParams(AdminAuthBase):
    """Parameters for define_agent."""

    name: str = pydantic.Field(description="Name for the agent, e.g. 'bookkeeper'.")
    description: str = pydantic.Field(
        description="What this agent handles. The delegating agent reads this to decide when to call it."
    )
    instructions: str = pydantic.Field(description="The agent's system instructions.")
    exposed_to: str = pydantic.Field(
        default=ROOT_AGENT,
        description=f"Agent that may delegate to this one. Defaults to '{ROOT_AGENT}'.",
    )


class DefineToolParams(AdminAuthBase):
    """Parameters for define_tool."""

    name: str = pydantic.Field(description="The name of the tool (will be callable as this name).")
    description: str = pydantic.Field(description="A brief description of what the tool does.")
    source_code: str = pydantic.Field(description="Python source code. " + TOOL_CONTRACT)
    agent: str = pydantic.Field(
        default=ROOT_AGENT,
        description=f"Which agent gets this tool. Defaults to '{ROOT_AGENT}'.",
    )


class ToolNameParams(AdminAuthBase):
    """Parameters for the tool operations that act on one tool by name."""

    name: str = pydantic.Field(description="The name of the tool.")


class DeleteAgentParams(AdminAuthBase):
    """Parameters for delete_agent."""

    name: str = pydantic.Field(
        description="Name of the agent to delete, together with every tool assigned to it."
    )


def list_admin_talkers(memory):
    """Every session granted admin. Empty means the device is unclaimed."""
    return list(memory.get("admingrant"))


def is_admin_talker(memory, talker):
    """Check if a talker has admin privileges."""
    if not talker:
        return False
    return bool(memory.get.admingrant(talker=talker))


def grant_admin_talker(memory, talker):
    """Grant admin privileges to a talker, persisting the grant."""
    if not talker:
        return False
    if not memory.get.admingrant(talker=talker):
        now = datetime.now(timezone.utc).isoformat()
        memory.put(AdminGrant(talker=talker, granted_at=now))
    return True


def load_tool_source(source_code):
    """Exec tool source and return ``(Params, handler, error)``.

    This is the only place the contract in TOOL_CONTRACT is enforced. Both the
    write path (define_tool) and the read path (build_dynamic_tool_specs)
    go through it, so a tool that was accepted when it was defined can always
    be loaded again later. ``error`` is a chat-facing string, or None on
    success.
    """
    namespace = {"pydantic": pydantic, "BaseModel": pydantic.BaseModel}
    try:
        exec(source_code, namespace)
    except SyntaxError as e:
        return None, None, f"Syntax error in source code: {e}"
    except ImportError as e:
        return None, None, f"Missing import: {e}. Install the package and try again."
    except Exception as e:
        return None, None, f"Error executing source code: {e}"

    params_model = namespace.get("Params")
    handler = namespace.get("handler")

    if params_model is None:
        return None, None, "Source code must define a 'Params' pydantic model."
    if not (isinstance(params_model, type) and issubclass(params_model, pydantic.BaseModel)):
        return None, None, "'Params' must be a pydantic.BaseModel subclass."
    if handler is None:
        return None, None, "Source code must define an async 'handler(ctx, params)' function."
    if not inspect.iscoroutinefunction(handler):
        return None, None, "'handler' must be declared with 'async def'."

    positional = [
        p
        for p in inspect.signature(handler).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) != 2:
        return None, None, (
            "'handler' must take exactly two positional arguments (ctx, params); "
            f"got {len(positional)}."
        )

    return params_model, handler, None


async def claim_admin(ctx: dict, params: AdminAuthBase) -> str:
    """Claim admin privileges if nobody has claimed them yet."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not memory or not talker:
        return "Internal error: no memory or talker context."

    if list(memory.get("admingrant")):
        return (
            "Admin has already been claimed. If you need to grant admin to another session, "
            "ask an existing admin to call grant_admin."
        )

    if grant_admin_talker(memory, talker):
        return f"Granted! You are now the admin for this bot. You can now call define_tool, set_instructions, etc."
    else:
        return "Failed to grant admin. Please try again."


async def grant_admin(ctx: dict, params: GrantAdminParams) -> str:
    """Grant admin to another session (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to grant admin to others. Call claim_admin first."

    if grant_admin_talker(memory, params.talker):
        return f"Granted admin to session {params.talker}."
    else:
        return f"Failed to grant admin to {params.talker}."


async def set_instructions(ctx: dict, params: SetInstructionsParams) -> str:
    """Set the bot's system instructions (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to set instructions. Call claim_admin first."

    try:
        now = datetime.now(timezone.utc).isoformat()
        config = memory.get.botconfig(name=params.agent)

        if config:
            config.instructions = params.text
            config.updated_at = now
        else:
            config = BotConfig(
                name=params.agent,
                instructions=params.text,
                updated_at=now,
            )
        memory.put(config)
        return f"Instructions updated for '{config.name}'."
    except Exception as e:
        return f"Failed to set instructions: {e}"


def get_provider(memory, name=None):
    """Return the named Provider row, or the default one; None if there is none."""
    if name:
        return memory.get.provider(name=name)
    for provider in memory.get("provider"):
        if provider.default:
            return provider
    return None


def save_provider(memory, name=None, api_key=None, model=None, default=False):
    """Create or update a Provider row, leaving unspecified fields alone.

    The single write path for providers, so validating the name here is enough
    for everything downstream to trust it. No name means the default provider.
    A new provider starts on its model from ``PROVIDERS``. The first provider
    saved becomes the default, and making one the default unsets the others.

    :raises ValueError: if ``name`` is not a provider the adapter can reach.
    """
    if name and name not in PROVIDERS:
        raise ValueError(name)

    provider = get_provider(memory, name)
    if not provider:
        provider = Provider(name=name, model=PROVIDERS[name])
    if default or not get_provider(memory):
        for other in memory.get("provider"):
            if other.default and other.name != provider.name:
                other.default = False
                memory.put(other)
        provider.default = True
    if api_key is not None:
        provider.api_key = api_key
    if model:
        provider.model = model
    provider.updated_at = datetime.now(timezone.utc).isoformat()
    memory.put(provider)
    return provider


def seed_agent(memory, name, description, instructions, exposed_to):
    """Create an agent if it does not exist yet, leaving an existing one alone."""
    if memory.get.botconfig(name=name):
        return None
    config = BotConfig(
        name=name,
        description=description,
        instructions=instructions,
        exposed_to=exposed_to,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    memory.put(config)
    return config


def clear_provider(memory):
    """Unset the default provider so first-boot setup runs again.

    Keys stay stored, so agents running on another provider keep working.
    """
    provider = get_provider(memory)
    if provider:
        provider.default = False
        memory.put(provider)


async def set_provider(ctx: dict, params: SetProviderParams) -> str:
    """Add a provider, or change its key or model, or the default (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to change providers. Call claim_admin first."
    if not (params.api_key or params.provider or params.model or params.default):
        providers = list(memory.get("provider"))
        if not providers:
            return "No provider configured yet."
        return "Providers:\n" + "\n".join(
            f"  - {p.name}{' (default)' if p.default else ''}: model {p.model}, "
            f"key ending {p.api_key[-4:]}"
            for p in providers
        )

    if params.provider and params.provider not in PROVIDERS:
        return (
            f"'{params.provider}' is not a provider I can reach. "
            f"Choose one of: {', '.join(PROVIDERS)}."
        )
    if params.provider and not get_provider(memory, params.provider) and not params.api_key:
        return f"'{params.provider}' has no key stored yet. Give its api key too."

    provider = save_provider(
        memory, params.provider, params.api_key or None, params.model, params.default
    )
    role = "the default" if provider.default else "available for set_model"
    return (
        f"{provider.name} runs {provider.model} and is {role}. "
        "It takes effect on the next message."
    )


async def set_model(ctx: dict, params: SetModelParams) -> str:
    """Run one agent on its own provider and model (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to change the model. Call claim_admin first."

    config = memory.get.botconfig(name=params.agent)
    if not config and params.agent != ROOT_AGENT:
        return f"No agent named '{params.agent}'."
    config = config or BotConfig(name=params.agent)

    model = params.model
    if model:
        name, slash, bare = model.partition("/")
        if not slash:
            name, bare = get_provider(memory).name, model
        provider = get_provider(memory, name)
        if not (provider and provider.api_key):
            return (
                f"No key is stored for '{name}'. Add one with set_provider first. "
                f"Providers I can reach: {', '.join(PROVIDERS)}."
            )
        model = f"{name}/{bare}"

    config.model = model
    config.updated_at = datetime.now(timezone.utc).isoformat()
    memory.put(config)
    if model:
        return f"'{params.agent}' now runs on {model}. It takes effect on the next message."
    return f"'{params.agent}' follows the default provider again."


async def delete_provider(ctx: dict, params: ProviderNameParams) -> str:
    """Forget a provider and its key (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to delete providers. Call claim_admin first."

    provider = get_provider(memory, params.name)
    if not provider:
        return f"No provider named '{params.name}'."
    if provider.default:
        return (
            f"'{params.name}' is the default provider. Make another one the default "
            "with set_provider first."
        )
    users = [
        config.name
        for config in memory.get("botconfig")
        if config.model.startswith(f"{params.name}/")
    ]
    if users:
        return (
            f"'{params.name}' is still used by: {', '.join(sorted(users))}. "
            "Move them with set_model first."
        )
    memory.delete(provider)
    return f"Provider '{params.name}' and its key are forgotten."


async def define_agent(ctx: dict, params: DefineAgentParams) -> str:
    """Create or update an agent that another agent can delegate to (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to define agents. Call claim_admin first."

    if params.name == params.exposed_to:
        return "An agent cannot delegate to itself."

    try:
        config = memory.get.botconfig(name=params.name) or BotConfig(name=params.name)
        config.description = params.description
        config.instructions = params.instructions
        config.exposed_to = params.exposed_to
        config.updated_at = datetime.now(timezone.utc).isoformat()
        memory.put(config)
        return (
            f"Agent '{params.name}' defined. "
            f"'{params.exposed_to}' can now delegate to it."
        )
    except Exception as e:
        return f"Failed to define agent: {e}"


async def list_agents(ctx: dict, params: AdminAuthBase) -> str:
    """List the agents and who may delegate to each."""
    memory = ctx.get("memory")
    configs = list(memory.get("botconfig"))

    if not configs:
        return "No agents configured yet."

    tools_by_agent = {}
    for tool in memory.get("dynamictool"):
        tools_by_agent.setdefault(tool.agent, []).append(tool.name)

    # main always exists, with or without a stored row of its own.
    existing = {config.name for config in configs} | {ROOT_AGENT}

    lines = []
    for config in configs:
        owned = ", ".join(sorted(tools_by_agent.get(config.name, []))) or "no tools"
        if config.name == ROOT_AGENT:
            where = "talks to people"
        elif config.exposed_to in existing:
            where = f"called by {config.exposed_to}"
        elif config.exposed_to:
            where = f"unreachable - '{config.exposed_to}' does not exist"
        else:
            where = "unreachable - no agent delegates to it"
        model = f", model {config.model}" if config.model else ""
        lines.append(f"  - {config.name} ({where}{model}): {owned}")

    return "Agents:\n" + "\n".join(lines)


async def define_tool(ctx: dict, params: DefineToolParams) -> str:
    """Define a new tool by providing Python source code (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to define tools. Call claim_admin first."

    _, _, error = load_tool_source(params.source_code)
    if error:
        return error

    try:
        now = datetime.now(timezone.utc).isoformat()
        tool = DynamicTool(
            name=params.name,
            description=params.description,
            source_code=params.source_code,
            enabled=True,
            created_by=talker,
            updated_at=now,
            contract_version=CONTRACT_VERSION,
            agent=params.agent,
        )
        memory.put(tool)
        return f"Tool '{params.name}' defined successfully for agent '{params.agent}'."
    except Exception as e:
        return f"Failed to persist tool: {e}"


async def list_tools(ctx: dict, params: AdminAuthBase) -> str:
    """List all defined tools."""
    memory = ctx.get("memory")

    tools = list(memory.get("dynamictool"))

    if not tools:
        return "No tools defined yet."

    lines = ["Defined tools:"]
    for tool in tools:
        status = "enabled" if tool.enabled else "disabled"
        lines.append(f"  - {tool.name} [{tool.agent}] ({status}): {tool.description}")

    return "\n".join(lines)


async def disable_tool(ctx: dict, params: ToolNameParams) -> str:
    """Disable a tool without deleting it (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to disable tools. Call claim_admin first."

    try:
        tool = memory.get.dynamictool(name=params.name)
        if not tool:
            return f"Tool '{params.name}' not found."
        tool.enabled = False
        memory.put(tool)
        return f"Tool '{params.name}' disabled. Use enable_tool to bring it back."
    except Exception as e:
        return f"Failed to disable tool: {e}"


async def enable_tool(ctx: dict, params: ToolNameParams) -> str:
    """Re-enable a disabled tool (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to enable tools. Call claim_admin first."

    tool = memory.get.dynamictool(name=params.name)
    if not tool:
        return f"Tool '{params.name}' not found."
    if tool.enabled:
        return f"Tool '{params.name}' is already enabled."
    tool.enabled = True
    memory.put(tool)
    return f"Tool '{params.name}' enabled."


async def delete_tool(ctx: dict, params: ToolNameParams) -> str:
    """Delete a tool and its stored source for good (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to delete tools. Call claim_admin first."

    tool = memory.get.dynamictool(name=params.name)
    if not tool:
        return f"Tool '{params.name}' not found."

    memory.delete(tool)
    return (
        f"Tool '{params.name}' deleted. Its source is gone - define_tool to recreate "
        "it, or use disable_tool next time to keep the source."
    )


async def delete_agent(ctx: dict, params: DeleteAgentParams) -> str:
    """Delete an agent and every tool assigned to it (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not is_admin_talker(memory, talker):
        return "You must be admin to delete agents. Call claim_admin first."

    if params.name == ROOT_AGENT:
        return (
            f"'{ROOT_AGENT}' is the agent people talk to, so it cannot be deleted. "
            "Use set_instructions to change what it does."
        )

    config = memory.get.botconfig(name=params.name)
    if not config:
        return f"No agent named '{params.name}'."

    doomed = [tool for tool in memory.get("dynamictool") if tool.agent == params.name]
    for tool in doomed:
        memory.delete(tool)
    memory.delete(config)

    message = f"Agent '{params.name}' deleted"
    if doomed:
        names = ", ".join(sorted(tool.name for tool in doomed))
        message += f", along with its {len(doomed)} tool(s): {names}."
    else:
        message += " (it had no tools)."

    # Anything that delegated from the deleted agent survives, but nothing can
    # reach it any more - say so rather than removing a subtree by surprise.
    orphans = sorted(
        c.name for c in memory.get("botconfig") if c.exposed_to == params.name
    )
    if orphans:
        message += (
            f" {', '.join(orphans)} delegated from it and can no longer be reached; "
            "re-point with define_agent or delete separately."
        )
    return message


def build_bootstrap_tool_specs() -> list[ToolSpec]:
    """Build the ToolSpec descriptors for all bootstrap tools."""
    return [
        ToolSpec(
            name="claim_admin",
            description="Claim admin privileges for this session if nobody has claimed them yet.",
            params=AdminAuthBase,
            handler=claim_admin,
        ),
        ToolSpec(
            name="grant_admin",
            description="Grant admin privileges to another session (admin-only).",
            params=GrantAdminParams,
            handler=grant_admin,
        ),
        ToolSpec(
            name="set_instructions",
            description="Set the bot's system instructions (admin-only).",
            params=SetInstructionsParams,
            handler=set_instructions,
        ),
        ToolSpec(
            name="define_tool",
            description=(
                "Define a new tool by providing Python source code (admin-only). "
                "Source must define a Params pydantic model and an async handler function."
            ),
            params=DefineToolParams,
            handler=define_tool,
        ),
        ToolSpec(
            name="set_provider",
            description=(
                "Add an LLM provider with its API key, change a provider's key or model, "
                "or choose the default provider (admin-only). Called with no arguments "
                "it lists the stored providers."
            ),
            params=SetProviderParams,
            handler=set_provider,
        ),
        ToolSpec(
            name="set_model",
            description=(
                "Run one agent on its own provider and model, given as provider/model "
                "(admin-only). The provider needs a key stored with set_provider. An "
                "empty model puts the agent back on the default provider."
            ),
            params=SetModelParams,
            handler=set_model,
        ),
        ToolSpec(
            name="delete_provider",
            description=(
                "Forget a provider and its API key (admin-only). Not possible for the "
                "default provider or one an agent still runs on."
            ),
            params=ProviderNameParams,
            handler=delete_provider,
        ),
        ToolSpec(
            name="define_agent",
            description=(
                "Create or update a specialist agent that this one can delegate to "
                "(admin-only). Give it its own instructions, then attach tools to it "
                "with define_tool(agent=...)."
            ),
            params=DefineAgentParams,
            handler=define_agent,
        ),
        ToolSpec(
            name="list_agents",
            description="List the agents, who may delegate to each, and the tools each owns.",
            params=AdminAuthBase,
            handler=list_agents,
        ),
        ToolSpec(
            name="list_tools",
            description="List all defined tools and their status.",
            params=AdminAuthBase,
            handler=list_tools,
        ),
        ToolSpec(
            name="disable_tool",
            description="Disable a tool but keep its source, so it can be enabled again (admin-only).",
            params=ToolNameParams,
            handler=disable_tool,
        ),
        ToolSpec(
            name="enable_tool",
            description="Re-enable a previously disabled tool (admin-only).",
            params=ToolNameParams,
            handler=enable_tool,
        ),
        ToolSpec(
            name="delete_tool",
            description="Delete a tool and its source for good (admin-only). Prefer disable_tool to keep the source.",
            params=ToolNameParams,
            handler=delete_tool,
        ),
        ToolSpec(
            name="delete_agent",
            description=(
                "Delete an agent and every tool assigned to it (admin-only). The agent "
                "people talk to cannot be deleted."
            ),
            params=DeleteAgentParams,
            handler=delete_agent,
        ),
    ]


def build_dynamic_tool_specs(memory, agent=ROOT_AGENT):
    """Load one agent's enabled DynamicTool rows as ToolSpec descriptors.

    Returns framework-neutral specs; binding them to an agent framework is the
    caller's job (see ``agent.build_agent``).
    """
    tools = [
        tool
        for tool in memory.get("dynamictool")
        if tool.enabled and tool.agent == agent
    ]

    specs = []
    for tool in tools:
        version = getattr(tool, "contract_version", CONTRACT_VERSION)
        if version != CONTRACT_VERSION:
            log.warning(
                "Skipping tool %r: written against contract version %s, this build speaks %s.",
                tool.name,
                version,
                CONTRACT_VERSION,
            )
            continue

        params_model, handler, error = load_tool_source(tool.source_code)
        if error:
            log.warning("Skipping tool %r: %s", tool.name, error)
            continue

        specs.append(
            ToolSpec(
                name=tool.name,
                description=tool.description,
                params=params_model,
                handler=handler,
            )
        )

    return specs
