"""Dynamic tool definitions, admin grants, and the bot configuration.

All of these are persisted via root.memory (a membank SQLite dataclass store).
"""

import dataclasses
from datetime import datetime

import pydantic

from .openai_tools import ToolSpec


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
    source_code: str = ""  # must define class Params and async def handler
    enabled: bool = True
    created_by: str = ""
    updated_at: str = ""


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
    source_code: str = pydantic.Field(
        description=(
            "Python source code. Must define: "
            "class Params(pydantic.BaseModel): ... "
            "async def handler(ctx, params) -> str: ..."
        )
    )


class DisableToolParams(AdminAuthBase):
    """Parameters for disable_tool."""

    name: str = pydantic.Field(description="The name of the tool to disable.")


# Helper functions for admin authorization

_admin_talker_cache = {}  # In-memory cache of talker -> bool (is admin)


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
            now = datetime.utcnow().isoformat()
            memory.put(AdminGrant(talker=talker, granted_at=now))
        return True
    except Exception:
        return False


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
        now = datetime.utcnow().isoformat()
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

    # Validate the source code by exec'ing it into a scratch namespace
    namespace = {"pydantic": pydantic, "BaseModel": pydantic.BaseModel}
    try:
        exec(params.source_code, namespace)
    except SyntaxError as e:
        return f"Syntax error in source code: {e}"
    except ImportError as e:
        return f"Missing import: {e}. Install the package and try again."
    except Exception as e:
        return f"Error executing source code: {e}"

    # Confirm Params and handler exist
    if "Params" not in namespace:
        return "Source code must define a 'Params' pydantic model."
    if "handler" not in namespace:
        return "Source code must define an async 'handler(ctx, params) -> str' function."

    Params = namespace["Params"]
    handler = namespace["handler"]

    # Validate that Params is a pydantic model
    if not (isinstance(Params, type) and issubclass(Params, pydantic.BaseModel)):
        return "Params must be a pydantic.BaseModel subclass."

    # Persist the tool
    try:
        now = datetime.utcnow().isoformat()
        tool = DynamicTool(
            name=params.name,
            description=params.description,
            source_code=params.source_code,
            enabled=True,
            created_by=talker,
            updated_at=now,
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


def build_dynamic_function_tools(memory):
    """Load all enabled DynamicTool rows and convert them to function tools."""
    from .openai_tools import to_function_tools

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
        namespace = {"pydantic": pydantic, "BaseModel": pydantic.BaseModel}
        try:
            exec(tool.source_code, namespace)
            Params = namespace.get("Params")
            handler = namespace.get("handler")
            if Params and handler:
                specs.append(
                    ToolSpec(
                        name=tool.name,
                        description=tool.description,
                        params=Params,
                        handler=handler,
                    )
                )
        except Exception:
            # Skip tools that fail to load
            pass

    ctx = {"memory": memory, "talker": ""}  # Placeholder context for dynamic tools
    return to_function_tools(specs, ctx)
