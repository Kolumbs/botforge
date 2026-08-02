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


@dataclasses.dataclass
class BotConfig:
    """One agent: its personality, model, and who may delegate to it."""

    name: str = dataclasses.field(default=ROOT_AGENT, metadata={"key": True})
    instructions: str = ""
    model: str = ""
    description: str = ""  # shown to the agent that calls this one as a tool
    exposed_to: str = ""   # agent that may delegate here; empty means nobody
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
    model: str = pydantic.Field(
        default="",
        description=(
            "(Optional) Model to use. A bare name such as gpt-4o-mini uses OpenAI; "
            "a provider-qualified name such as anthropic/claude-opus-5 uses LiteLLM. "
            "Leave empty to keep the current one."
        ),
    )
    agent: str = pydantic.Field(
        default=ROOT_AGENT,
        description=f"Which agent to configure. Defaults to '{ROOT_AGENT}', the one people talk to.",
    )


class DefineAgentParams(AdminAuthBase):
    """Parameters for define_agent."""

    name: str = pydantic.Field(description="Name for the agent, e.g. 'bookkeeper'.")
    description: str = pydantic.Field(
        description="What this agent handles. The delegating agent reads this to decide when to call it."
    )
    instructions: str = pydantic.Field(description="The agent's system instructions.")
    model: str = pydantic.Field(default="", description="(Optional) Model for this agent.")
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


class DisableToolParams(AdminAuthBase):
    """Parameters for disable_tool."""

    name: str = pydantic.Field(description="The name of the tool to disable.")


def _is_admin_talker(memory, talker):
    """Check if a talker has admin privileges."""
    if not talker:
        return False
    return bool(memory.get.admingrant(talker=talker))


def _grant_admin(memory, talker):
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

    if _grant_admin(memory, talker):
        return f"Granted! You are now the admin for this bot. You can now call define_tool, set_instructions, etc."
    else:
        return "Failed to grant admin. Please try again."


async def grant_admin(ctx: dict, params: GrantAdminParams) -> str:
    """Grant admin to another session (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
        return "You must be admin to grant admin to others. Call claim_admin first."

    if _grant_admin(memory, params.talker):
        return f"Granted admin to session {params.talker}."
    else:
        return f"Failed to grant admin to {params.talker}."


async def set_instructions(ctx: dict, params: SetInstructionsParams) -> str:
    """Set the bot's system instructions (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
        return "You must be admin to set instructions. Call claim_admin first."

    try:
        now = datetime.now(timezone.utc).isoformat()
        config = memory.get.botconfig(name=params.agent)

        if config:
            config.instructions = params.text
            if params.model:
                config.model = params.model
            config.updated_at = now
        else:
            config = BotConfig(
                name=params.agent,
                instructions=params.text,
                model=params.model,
                updated_at=now,
            )
        memory.put(config)
        return f"Instructions updated for '{config.name}'."
    except Exception as e:
        return f"Failed to set instructions: {e}"


async def define_agent(ctx: dict, params: DefineAgentParams) -> str:
    """Create or update an agent that another agent can delegate to (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
        return "You must be admin to define agents. Call claim_admin first."

    if params.name == params.exposed_to:
        return "An agent cannot delegate to itself."

    try:
        config = memory.get.botconfig(name=params.name) or BotConfig(name=params.name)
        config.description = params.description
        config.instructions = params.instructions
        config.exposed_to = params.exposed_to
        if params.model:
            config.model = params.model
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

    lines = []
    for config in configs:
        owned = ", ".join(sorted(tools_by_agent.get(config.name, []))) or "no tools"
        if config.name == ROOT_AGENT:
            where = "talks to people"
        elif config.exposed_to:
            where = f"called by {config.exposed_to}"
        else:
            where = "not reachable - no agent delegates to it"
        lines.append(f"  - {config.name} ({where}): {owned}")

    return "Agents:\n" + "\n".join(lines)


async def define_tool(ctx: dict, params: DefineToolParams) -> str:
    """Define a new tool by providing Python source code (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
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


async def disable_tool(ctx: dict, params: DisableToolParams) -> str:
    """Disable a tool without deleting it (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
        return "You must be admin to disable tools. Call claim_admin first."

    try:
        tool = memory.get.dynamictool(name=params.name)
        if not tool:
            return f"Tool '{params.name}' not found."
        tool.enabled = False
        memory.put(tool)
        return f"Tool '{params.name}' disabled."
    except Exception as e:
        return f"Failed to disable tool: {e}"


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
            description="Set the bot's system instructions and optionally the model (admin-only).",
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
            description="Disable a tool without deleting it (admin-only).",
            params=DisableToolParams,
            handler=disable_tool,
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
