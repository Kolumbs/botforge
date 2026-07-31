"""Dynamic tool definitions, admin grants, and the bot configuration.

All of these are persisted via root.memory (a membank SQLite dataclass store).
"""

import dataclasses
import inspect
import logging
from datetime import datetime, timezone

import pydantic

from .tools import ToolSpec


log = logging.getLogger(__name__)


# The dynamic-tool contract. Stored tool source lives in the database, so this
# shape cannot be changed by editing source files - every tool ever authored by
# chat is written against it. Bump CONTRACT_VERSION if it ever has to change,
# and migrate or re-author the stored rows.
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


@dataclasses.dataclass
class BotConfig:
    """Singleton row (id=1) holding the bot's instructions and model choice."""

    id: int = dataclasses.field(default=1, metadata={"key": True})
    instructions: str = ""
    model: str = "gpt-4o-mini"
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
    description: str = ""
    source_code: str = ""  # written against the contract in TOOL_CONTRACT
    enabled: bool = True
    created_by: str = ""
    updated_at: str = ""
    # Which contract revision the source was written against, so a tool stored
    # under an older shape is skipped loudly instead of exec'd blindly.
    contract_version: int = CONTRACT_VERSION


# Bootstrap tool parameter models


class AdminAuthBase(pydantic.BaseModel):
    """Base params for admin-gated operations."""

    pass  # No secret field — admin-ness is session/talker-keyed, not secret-based.


class GrantAdminParams(AdminAuthBase):
    """Parameters for grant_admin."""

    talker: str = pydantic.Field(description="The session ID (talker) to grant admin to.")


class SetInstructionsParams(AdminAuthBase):
    """Parameters for set_instructions."""

    text: str = pydantic.Field(description="The bot's new system instructions/personality.")
    model: str = pydantic.Field(
        default="",
        description="(Optional) The OpenAI model to use (e.g., gpt-4o-mini). Leave empty to keep current.",
    )


class DefineToolParams(AdminAuthBase):
    """Parameters for define_tool."""

    name: str = pydantic.Field(description="The name of the tool (will be callable as this name).")
    description: str = pydantic.Field(description="A brief description of what the tool does.")
    source_code: str = pydantic.Field(description="Python source code. " + TOOL_CONTRACT)


class DisableToolParams(AdminAuthBase):
    """Parameters for disable_tool."""

    name: str = pydantic.Field(description="The name of the tool to disable.")


# Helper functions for admin authorization



def _is_admin_talker(memory, talker):
    """Check if a talker has admin privileges."""
    if not talker:
        return False
    try:
        grant = memory.get.admin_grant(talker=talker)
        return bool(grant)
    except Exception:
        return False


def _grant_admin(memory, talker):
    """Grant admin privileges to a talker, persisting the grant."""
    if not talker:
        return False
    try:
        grant = memory.get.admin_grant(talker=talker)
        if not grant:
            now = datetime.now(timezone.utc).isoformat()
            memory.put(AdminGrant(talker=talker, granted_at=now))
        return True
    except Exception:
        return False


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


# Bootstrap tools (always registered, the mechanism that makes everything else possible)


async def claim_admin(ctx: dict, params: AdminAuthBase) -> str:
    """Claim admin privileges if nobody has claimed them yet."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not memory or not talker:
        return "Internal error: no memory or talker context."

    try:
        existing_grants = list(memory.get_filter(AdminGrant, filter=None))
    except Exception:
        existing_grants = []

    if existing_grants:
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
        try:
            config = memory.get.bot_config(id=1)
        except Exception:
            config = None

        if config:
            config.instructions = params.text
            if params.model:
                config.model = params.model
            config.updated_at = now
        else:
            config = BotConfig(
                id=1,
                instructions=params.text,
                model=params.model or "gpt-4o-mini",
                updated_at=now,
            )
        memory.put(config)
        return f"Instructions updated. Model: {config.model}."
    except Exception as e:
        return f"Failed to set instructions: {e}"


async def define_tool(ctx: dict, params: DefineToolParams) -> str:
    """Define a new tool by providing Python source code (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
        return "You must be admin to define tools. Call claim_admin first."

    # Validate against the contract before persisting anything, so a broken
    # tool is reported in chat rather than stored and skipped later.
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
        )
        memory.put(tool)
        # Bump the tools version so the plugin knows to rebuild the agent
        ctx["tools_version_updated"] = True
        return f"Tool '{params.name}' defined successfully."
    except Exception as e:
        return f"Failed to persist tool: {e}"


async def list_tools(ctx: dict, params: AdminAuthBase) -> str:
    """List all defined tools."""
    memory = ctx.get("memory")

    try:
        tools = list(memory.get_filter(DynamicTool, filter=None))
    except Exception:
        tools = []

    if not tools:
        return "No tools defined yet."

    lines = ["Defined tools:"]
    for tool in tools:
        status = "enabled" if tool.enabled else "disabled"
        lines.append(f"  - {tool.name} ({status}): {tool.description}")

    return "\n".join(lines)


async def disable_tool(ctx: dict, params: DisableToolParams) -> str:
    """Disable a tool without deleting it (admin-only)."""
    memory = ctx.get("memory")
    talker = ctx.get("talker")

    if not _is_admin_talker(memory, talker):
        return "You must be admin to disable tools. Call claim_admin first."

    try:
        try:
            tool = memory.get.dynamic_tool(name=params.name)
        except Exception:
            tool = None

        if not tool:
            return f"Tool '{params.name}' not found."
        tool.enabled = False
        memory.put(tool)
        ctx["tools_version_updated"] = True
        return f"Tool '{params.name}' disabled."
    except Exception as e:
        return f"Failed to disable tool: {e}"


def _build_bootstrap_tool_specs() -> list[ToolSpec]:
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


def build_dynamic_tool_specs(memory):
    """Load enabled DynamicTool rows and return them as ToolSpec descriptors.

    Returns framework-neutral specs; binding them to an agent framework is the
    caller's job (see ``agent.build_agent``).
    """
    try:
        # Try to get all DynamicTool rows — membank pattern may vary
        try:
            tools = list(memory.get_filter(DynamicTool, filter=None))
        except Exception:
            try:
                tools = list(memory.get("dynamic_tool"))
            except Exception:
                tools = []
        tools = [t for t in tools if t.enabled]
    except Exception:
        tools = []

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
            # Stored source that no longer loads - surfaced here rather than
            # silently vanishing from the agent's tool list.
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
