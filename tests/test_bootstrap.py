"""Unit tests for the botforge bootstrap mechanism, tool contract and session."""

import dataclasses
import re
from unittest.mock import patch

import pytest

from botforge.dynamic_tools import (
    CONTRACT_VERSION,
    AdminAuthBase,
    AdminGrant,
    BotConfig,
    DefineToolParams,
    DisableToolParams,
    DynamicTool,
    GrantAdminParams,
    SetInstructionsParams,
    build_dynamic_tool_specs,
    claim_admin,
    define_tool,
    disable_tool,
    grant_admin,
    list_tools,
    load_tool_source,
    set_instructions,
)
from botforge.session import WindowedSession


ECHO_TOOL = """
import pydantic

class Params(pydantic.BaseModel):
    message: str = pydantic.Field(description="A message to echo")

async def handler(ctx, params) -> str:
    return f"Echo: {params.message}"
"""


def _table_name(cls):
    """membank derives a table name from the dataclass name."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()


def _primary_key(obj):
    for field in dataclasses.fields(obj):
        if field.metadata.get("key"):
            return getattr(obj, field.name)
    return None


class _Getter:
    """Emulates membank's ``memory.get.<table>(**filters)`` accessor."""

    def __init__(self, store):
        self._store = store

    def __getattr__(self, table):
        def lookup(**filters):
            for obj in self._store.rows:
                if _table_name(type(obj)) != table:
                    continue
                if all(getattr(obj, k, None) == v for k, v in filters.items()):
                    return obj
            return None

        return lookup


class MockMemory:
    """In-memory stand-in for membank.LoadMemory."""

    def __init__(self):
        self.rows = []
        self.get = _Getter(self)

    def put(self, obj):
        key = _primary_key(obj)
        for index, existing in enumerate(self.rows):
            if type(existing) is type(obj) and _primary_key(existing) == key:
                self.rows[index] = obj
                return
        self.rows.append(obj)

    def get_filter(self, model_class, filter=None):
        return [row for row in self.rows if isinstance(row, model_class)]


@pytest.fixture
def memory():
    return MockMemory()


@pytest.fixture
def admin_memory(memory):
    memory.put(AdminGrant(talker="admin_session", granted_at="2026-01-01"))
    return memory


def _ctx(memory, talker):
    return {"memory": memory, "talker": talker}


# Admin bootstrap


@pytest.mark.asyncio
async def test_claim_admin_grants_first_caller(memory):
    result = await claim_admin(_ctx(memory, "session_123"), AdminAuthBase())

    assert "Granted" in result
    assert memory.get.admin_grant(talker="session_123") is not None


@pytest.mark.asyncio
async def test_claim_admin_rejects_second_caller(admin_memory):
    result = await claim_admin(_ctx(admin_memory, "someone_else"), AdminAuthBase())

    assert "already been claimed" in result
    assert admin_memory.get.admin_grant(talker="someone_else") is None


@pytest.mark.asyncio
async def test_grant_admin_is_admin_only(admin_memory):
    params = GrantAdminParams(talker="new_session")

    denied = await grant_admin(_ctx(admin_memory, "not_admin"), params)
    assert "must be admin" in denied
    assert admin_memory.get.admin_grant(talker="new_session") is None

    allowed = await grant_admin(_ctx(admin_memory, "admin_session"), params)
    assert "Granted" in allowed
    assert admin_memory.get.admin_grant(talker="new_session") is not None


@pytest.mark.asyncio
async def test_set_instructions_is_admin_only(admin_memory):
    params = SetInstructionsParams(text="Hello world", model="gpt-4")

    denied = await set_instructions(_ctx(admin_memory, "not_admin"), params)
    assert "must be admin" in denied
    assert admin_memory.get.bot_config(id=1) is None

    await set_instructions(_ctx(admin_memory, "admin_session"), params)
    config = admin_memory.get.bot_config(id=1)
    assert config.instructions == "Hello world"
    assert config.model == "gpt-4"


# Tool contract


@pytest.mark.asyncio
async def test_define_tool_persists_valid_source(admin_memory):
    params = DefineToolParams(
        name="echo", description="Echoes a message back", source_code=ECHO_TOOL
    )

    result = await define_tool(_ctx(admin_memory, "admin_session"), params)

    assert "successfully" in result
    stored = admin_memory.get.dynamic_tool(name="echo")
    assert stored.enabled is True
    assert stored.contract_version == CONTRACT_VERSION
    assert stored.created_by == "admin_session"


@pytest.mark.asyncio
async def test_define_tool_is_admin_only(memory):
    params = DefineToolParams(name="echo", description="d", source_code=ECHO_TOOL)

    result = await define_tool(_ctx(memory, "nobody"), params)

    assert "must be admin" in result
    assert memory.get.dynamic_tool(name="echo") is None


@pytest.mark.parametrize(
    "source, expected",
    [
        ("this is not valid python !!! @#$", "Syntax error"),
        ("async def handler(ctx, params) -> str:\n    return 'x'\n", "must define a 'Params'"),
        (
            "import pydantic\n"
            "class Params(pydantic.BaseModel):\n    x: str = ''\n"
            "async def wrong_name(ctx, params):\n    return 'x'\n",
            "must define an async 'handler",
        ),
        (
            "import pydantic\n"
            "class Params(pydantic.BaseModel):\n    x: str = ''\n"
            "def handler(ctx, params):\n    return 'x'\n",
            "async def",
        ),
        (
            "import pydantic\n"
            "class Params(pydantic.BaseModel):\n    x: str = ''\n"
            "async def handler(params):\n    return 'x'\n",
            "two positional arguments",
        ),
        (
            "class Params:\n    pass\n"
            "async def handler(ctx, params):\n    return 'x'\n",
            "pydantic.BaseModel subclass",
        ),
    ],
)
def test_load_tool_source_rejects_contract_violations(source, expected):
    params_model, handler, error = load_tool_source(source)

    assert params_model is None and handler is None
    assert expected in error


def test_load_tool_source_accepts_valid_source():
    params_model, handler, error = load_tool_source(ECHO_TOOL)

    assert error is None
    assert params_model.model_json_schema()["properties"]["message"]["type"] == "string"


@pytest.mark.asyncio
async def test_define_tool_rejects_and_stores_nothing_on_bad_source(admin_memory):
    sync_handler = (
        "import pydantic\n"
        "class Params(pydantic.BaseModel):\n    x: str = ''\n"
        "def handler(ctx, params):\n    return 'x'\n"
    )
    params = DefineToolParams(name="broken", description="d", source_code=sync_handler)

    result = await define_tool(_ctx(admin_memory, "admin_session"), params)

    assert "async def" in result
    assert admin_memory.get.dynamic_tool(name="broken") is None


@pytest.mark.asyncio
async def test_defined_tool_round_trips_and_is_callable(admin_memory):
    await define_tool(
        _ctx(admin_memory, "admin_session"),
        DefineToolParams(name="echo", description="d", source_code=ECHO_TOOL),
    )

    specs = build_dynamic_tool_specs(admin_memory)

    assert [spec.name for spec in specs] == ["echo"]
    spec = specs[0]
    result = await spec.handler({}, spec.params(message="hi"))
    assert result == "Echo: hi"


def test_loader_skips_tool_from_a_different_contract_version(memory):
    memory.put(
        DynamicTool(
            name="stale",
            description="d",
            source_code=ECHO_TOOL,
            contract_version=CONTRACT_VERSION + 1,
        )
    )

    assert build_dynamic_tool_specs(memory) == []


def test_loader_skips_source_that_no_longer_loads(memory):
    memory.put(DynamicTool(name="rotten", description="d", source_code="import nope_missing"))

    assert build_dynamic_tool_specs(memory) == []


# Tool management


@pytest.mark.asyncio
async def test_list_tools_reports_status(memory):
    memory.put(DynamicTool(name="tool1", description="First", enabled=True))
    memory.put(DynamicTool(name="tool2", description="Second", enabled=False))

    result = await list_tools(_ctx(memory, "anyone"), AdminAuthBase())

    assert "tool1 (enabled)" in result
    assert "tool2 (disabled)" in result


@pytest.mark.asyncio
async def test_disable_tool_is_admin_only(admin_memory):
    admin_memory.put(DynamicTool(name="tool1", description="d", enabled=True))
    params = DisableToolParams(name="tool1")

    denied = await disable_tool(_ctx(admin_memory, "not_admin"), params)
    assert "must be admin" in denied
    assert admin_memory.get.dynamic_tool(name="tool1").enabled is True

    await disable_tool(_ctx(admin_memory, "admin_session"), params)
    assert admin_memory.get.dynamic_tool(name="tool1").enabled is False
    assert build_dynamic_tool_specs(admin_memory) == []


# Conversation history


@pytest.fixture
def session(tmp_path):
    return WindowedSession("talker-1", str(tmp_path / "history.db"), window_size=4)


@pytest.mark.asyncio
async def test_session_round_trips_items(session):
    await session.add_items([{"role": "user", "content": "hi"}])

    assert await session.get_items() == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_session_windows_to_last_n_items(session):
    await session.add_items(
        [{"role": "user", "content": str(n)} for n in range(10)]
    )

    items = await session.get_items()

    assert [item["content"] for item in items] == ["6", "7", "8", "9"]


@pytest.mark.asyncio
async def test_session_window_starts_on_a_user_turn(session):
    await session.add_items(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "c"},
        ]
    )

    items = await session.get_items()

    # The 4-item window would start mid assistant run; it is trimmed forward.
    assert [item["role"] for item in items] == ["user", "assistant"]
    assert items[0]["content"] == "second"


@pytest.mark.asyncio
async def test_session_pop_and_clear(session):
    await session.add_items(
        [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}]
    )

    assert (await session.pop_item())["content"] == "two"
    assert len(await session.get_items()) == 1

    await session.clear_session()
    assert await session.get_items() == []
    assert await session.pop_item() is None


@pytest.mark.asyncio
async def test_sessions_are_isolated_by_talker(tmp_path):
    db = str(tmp_path / "shared.db")
    first = WindowedSession("talker-a", db)
    second = WindowedSession("talker-b", db)

    await first.add_items([{"role": "user", "content": "mine"}])

    assert await second.get_items() == []
    assert len(await first.get_items()) == 1


# Agent assembly (needs the agent framework installed)


def test_unconfigured_prompt_is_the_fallback(memory):
    from botforge.agent import UNCONFIGURED_PROMPT, build_agent

    with patch("botforge.agent.set_default_openai_key"):
        agent = build_agent({"api_key": "test-key"}, memory)

    assert UNCONFIGURED_PROMPT in agent.instructions


def test_stored_config_overrides_the_fallback(memory):
    from botforge.agent import build_agent

    memory.put(BotConfig(id=1, instructions="Custom personality", model="gpt-4"))

    with patch("botforge.agent.set_default_openai_key"):
        agent = build_agent({"api_key": "test-key"}, memory)

    assert "Custom personality" in agent.instructions
    assert agent.model == "gpt-4"


def test_agent_exposes_bootstrap_and_dynamic_tools(memory):
    from botforge.agent import build_agent

    memory.put(DynamicTool(name="echo", description="d", source_code=ECHO_TOOL))

    with patch("botforge.agent.set_default_openai_key"):
        agent = build_agent({"api_key": "test-key"}, memory)

    names = {tool.name for tool in agent.tools}
    assert "claim_admin" in names and "define_tool" in names
    assert "echo" in names
