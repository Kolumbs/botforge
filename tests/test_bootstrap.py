"""Unit tests for botforge bootstrap mechanism."""

import dataclasses
import tempfile
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
import pydantic

from botforge.agent import build_agent, UNCONFIGURED_PROMPT
from botforge.dynamic_tools import (
    AdminAuthBase,
    BotConfig,
    AdminGrant,
    DynamicTool,
    claim_admin,
    grant_admin,
    set_instructions,
    define_tool,
    list_tools,
    disable_tool,
    _is_admin_talker,
)


# Mock membank.LoadMemory for testing
class MockMemory:
    """A simple in-memory mock of membank.LoadMemory."""

    def __init__(self):
        self.data = {}

    def get(self, **kwargs):
        """Simulate membank's .get method (returns a single object or None)."""
        # This is a simplified mock; real membank has different signatures per type
        for key, value in self.data.items():
            if all(getattr(value, k, None) == v for k, v in kwargs.items()):
                return value
        return None

    def get_filter(self, model_class, filter=None):
        """Simulate membank's .get_filter method (returns a list)."""
        result = []
        for value in self.data.values():
            if isinstance(value, model_class):
                result.append(value)
        return result

    def put(self, obj):
        """Simulate membank's .put method (persists an object)."""
        key = getattr(obj, "id", None) or getattr(obj, "name", None) or getattr(obj, "talker", None)
        self.data[key] = obj

    def get_admin_grant(self, **kwargs):
        """Helper for admin grant lookups."""
        return self.get(AdminGrant, **kwargs)

    def get_bot_config(self, **kwargs):
        """Helper for bot config lookups."""
        return self.get(BotConfig, **kwargs)

    def get_dynamic_tool(self, **kwargs):
        """Helper for dynamic tool lookups."""
        return self.get(DynamicTool, **kwargs)


@pytest.mark.asyncio
async def test_claim_admin_first_caller():
    """First caller to claim_admin should be granted."""
    memory = MockMemory()
    ctx = {"memory": memory, "talker": "session_123"}
    params = AdminAuthBase()

    result = await claim_admin(ctx, params)

    assert "Granted" in result
    assert memory.get_admin_grant(talker="session_123") is not None


@pytest.mark.asyncio
async def test_claim_admin_already_claimed():
    """Subsequent callers should be rejected."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="session_first", granted_at="2024-01-01"))

    ctx = {"memory": memory, "talker": "session_second"}
    params = AdminAuthBase()

    result = await claim_admin(ctx, params)

    assert "already been claimed" in result
    assert memory.get_admin_grant(talker="session_second") is None


@pytest.mark.asyncio
async def test_grant_admin_by_admin_only():
    """Only an admin can grant admin to others."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    # Non-admin tries to grant
    ctx = {"memory": memory, "talker": "non_admin_session"}
    params = pydantic.BaseModel().parse_obj({"talker": "new_session"})

    from botforge.dynamic_tools import GrantAdminParams
    result = await grant_admin(ctx, GrantAdminParams(talker="new_session"))

    assert "must be admin" in result
    assert memory.get_admin_grant(talker="new_session") is None

    # Admin can grant
    ctx = {"memory": memory, "talker": "admin_session"}
    result = await grant_admin(ctx, GrantAdminParams(talker="new_session"))

    assert "Granted" in result
    assert memory.get_admin_grant(talker="new_session") is not None


@pytest.mark.asyncio
async def test_set_instructions_admin_only():
    """Only an admin can set instructions."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    # Non-admin tries
    ctx = {"memory": memory, "talker": "non_admin"}
    from botforge.dynamic_tools import SetInstructionsParams
    params = SetInstructionsParams(text="Hello world", model="gpt-4")

    result = await set_instructions(ctx, params)
    assert "must be admin" in result

    # Admin succeeds
    ctx = {"memory": memory, "talker": "admin_session"}
    result = await set_instructions(ctx, params)
    assert "updated" in result or "Instructions" in result

    # Verify it was stored
    config = memory.get_bot_config(id=1)
    assert config is not None
    assert config.instructions == "Hello world"
    assert config.model == "gpt-4"


@pytest.mark.asyncio
async def test_define_tool_with_valid_source():
    """A valid tool definition should be persisted."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    ctx = {"memory": memory, "talker": "admin_session"}

    source_code = '''
import pydantic

class Params(pydantic.BaseModel):
    message: str = pydantic.Field(description="A message to echo")

async def handler(ctx, params) -> str:
    return f"Echo: {params.message}"
'''

    from botforge.dynamic_tools import DefineToolParams
    params = DefineToolParams(
        name="echo",
        description="Echoes a message back",
        source_code=source_code,
    )

    result = await define_tool(ctx, params)
    assert "successfully" in result

    # Verify it was stored
    tool = memory.get_dynamic_tool(name="echo")
    assert tool is not None
    assert tool.description == "Echoes a message back"
    assert tool.enabled is True


@pytest.mark.asyncio
async def test_define_tool_with_invalid_syntax():
    """Invalid source code should return an error without persisting."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    ctx = {"memory": memory, "talker": "admin_session"}

    source_code = "this is not valid python !!! @#$"

    from botforge.dynamic_tools import DefineToolParams
    params = DefineToolParams(
        name="broken",
        description="A broken tool",
        source_code=source_code,
    )

    result = await define_tool(ctx, params)
    assert "Syntax error" in result or "Error" in result

    # Verify it was not stored
    tool = memory.get_dynamic_tool(name="broken")
    assert tool is None


@pytest.mark.asyncio
async def test_define_tool_missing_params():
    """Tool must define a Params class."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    ctx = {"memory": memory, "talker": "admin_session"}

    source_code = '''
async def handler(ctx, params) -> str:
    return "No Params defined"
'''

    from botforge.dynamic_tools import DefineToolParams
    params = DefineToolParams(
        name="no_params",
        description="Missing Params",
        source_code=source_code,
    )

    result = await define_tool(ctx, params)
    assert "must define a 'Params'" in result


@pytest.mark.asyncio
async def test_define_tool_missing_handler():
    """Tool must define an async handler."""
    memory = MockMemory()
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    ctx = {"memory": memory, "talker": "admin_session"}

    source_code = '''
import pydantic

class Params(pydantic.BaseModel):
    message: str

async def wrong_name(ctx, params) -> str:
    return "Wrong name"
'''

    from botforge.dynamic_tools import DefineToolParams
    params = DefineToolParams(
        name="no_handler",
        description="Missing handler",
        source_code=source_code,
    )

    result = await define_tool(ctx, params)
    assert "must define an async 'handler'" in result


@pytest.mark.asyncio
async def test_list_tools():
    """list_tools should show enabled and disabled tools."""
    memory = MockMemory()
    memory.put(DynamicTool(name="tool1", description="First tool", enabled=True))
    memory.put(DynamicTool(name="tool2", description="Second tool", enabled=False))

    ctx = {"memory": memory, "talker": "session"}
    params = AdminAuthBase()

    result = await list_tools(ctx, params)

    assert "tool1" in result
    assert "enabled" in result.lower()
    assert "tool2" in result
    assert "disabled" in result.lower()


@pytest.mark.asyncio
async def test_disable_tool_admin_only():
    """Only admin can disable tools."""
    memory = MockMemory()
    memory.put(DynamicTool(name="tool1", description="A tool", enabled=True))
    memory.put(AdminGrant(talker="admin_session", granted_at="2024-01-01"))

    # Non-admin tries
    ctx = {"memory": memory, "talker": "non_admin"}
    from botforge.dynamic_tools import DisableToolParams
    params = DisableToolParams(name="tool1")

    result = await disable_tool(ctx, params)
    assert "must be admin" in result

    # Tool is still enabled
    tool = memory.get_dynamic_tool(name="tool1")
    assert tool.enabled is True

    # Admin succeeds
    ctx = {"memory": memory, "talker": "admin_session"}
    result = await disable_tool(ctx, params)
    assert "disabled" in result

    # Tool is now disabled
    tool = memory.get_dynamic_tool(name="tool1")
    assert tool.enabled is False


def test_unconfigured_prompt_fallback():
    """A fresh database should produce the generic unconfigured prompt."""
    memory = MockMemory()
    conf = {"botforge": {"api_key": "test-key"}}

    with patch("botforge.agent.set_default_openai_key"):
        agent = build_agent(conf, memory)

    assert UNCONFIGURED_PROMPT in agent.instructions


def test_custom_instructions_override():
    """Custom instructions from BotConfig should override the default."""
    memory = MockMemory()
    memory.put(BotConfig(id=1, instructions="Custom bot personality", model="gpt-4"))
    conf = {"botforge": {"api_key": "test-key"}}

    with patch("botforge.agent.set_default_openai_key"):
        agent = build_agent(conf, memory)

    assert "Custom bot personality" in agent.instructions
    assert agent.model == "gpt-4"
