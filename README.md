# botforge

A chatbot platform where bot personality and capabilities are entirely database content, authored and updated by chatting with the bot. No code changes or redeployment needed to extend or modify a bot's behavior.

## Architecture

**botforge** ships as a generic, empty platform:

- Core modules handle the boilerplate: admin bootstrap, dynamic tool loading, agent assembly, session management.
- A bot's **instructions** (system prompt) and **tools** (function definitions) live entirely in SQLite as dataclass rows.
- An authorized admin can `claim_admin`, then teach the bot new capabilities by calling `define_tool` (with Python source code) and `set_instructions` (the bot's personality), all via conversation.

## Getting started

1. Install: `pip install -e .` (depends on `zoozl>=0.2.9` and `openai-agents`).
2. Configure: Create a TOML config file (e.g., `my_bot.toml`) with:
   ```toml
   [botforge]
   api_key = "your-openai-api-key"
   aliases = ["bot", "help", "greet"]
   session_database = "my_bot_sessions.db"
   
   [slack]
   signing_secret = "..."
   workspace_token = "..."
   ```
3. Run: `python -m zoozl my_bot.toml` (the config needs `extensions = ["botforge.plugin"]`)
4. Chat: Say hello to the bot, claim admin, and start teaching it.

## Project layout

```
botforge/
└── botforge/
    ├── __init__.py         # Empty by design - keeps the layers below SDK-free
    ├── tools.py            # ToolSpec: framework-neutral tool descriptor   ─┐
    ├── dynamic_tools.py    # Dataclasses, bootstrap tools, tool contract    ├─ no SDK
    ├── session.py          # Sliding-window conversation history           ─┘
    ├── openai_tools.py     # ToolSpec → agents.FunctionTool adapter        ─┐
    ├── agent.py            # build_agent(): assembles instructions + tools  ├─ framework
    └── plugin.py           # Bot(Interface): zoozl wiring, the agent loop  ─┘
```

Only the bottom three touch the agent framework, so swapping it means rewriting one
adapter plus two call sites.

## Bootstrap tools

Every bot instance ships with these built-in tools:

- `claim_admin()` — Mark the current session as the bot's admin (first-come, first-served).
- `grant_admin(talker)` — Add another session ID as admin (admin-only).
- `set_instructions(text, model?)` — Set the bot's system prompt and optionally the model (admin-only).
- `define_tool(name, description, source_code)` — Add a new tool by providing Python source (admin-only). Source must define a `Params` pydantic model and an async `handler` function.
- `list_tools()` — Show all defined tools and their status.
- `disable_tool(name)` — Disable a tool without deleting it (admin-only).

## How it works

1. **Persistence**: `root.memory` (a zoozl feature, powered by `membank`) is a SQLite dataclass ORM shared across all plugins. BotConfig, DynamicTool, and AdminGrant rows live there.
2. **Bootstrap tools** (hardcoded in `agent.py`) are always registered — they're the mechanism that lets everything else exist.
3. **Dynamic tools** (from DynamicTool rows) are loaded each turn and mixed into the agent's tool list. No process restart needed for pure-Python changes.
4. **Admin gate**: Every administrative tool checks if the current session's `talker` (a zoozl cookie-based session ID) is in the AdminGrant table. The first person to call `claim_admin` is granted forever.
5. **Tool code execution**: When a dynamic tool is invoked, its stored Python `source_code` is `exec`'d into a namespace, the `Params` class and `handler` function are extracted, and the tool behaves like any other function tool.

## Security notes

- **No sandboxing**: Tool code runs with full process privileges. Admin access = code execution. This is intentional — you own the bot and the server.
- **Session identity via cookie**: `talker` is a long-lived browser cookie, not a real user account. Anyone with access to that cookie can act as that session.
- **No dependency management yet**: If a tool needs a pip package that isn't installed, `define_tool` will return an error. A future enhancement (the "Rust supervisor" phase) will handle auto-installation.

## Testing

There is no standing test suite. Tests get written when a bug is identified, and
they target that specific bug — the goal is regression coverage for things that
actually broke, not scaffolding maintained alongside development.

When you do add one, install the dev extra and note that async tests need
`asyncio_mode = auto` (a `pytest.ini` with that line, or the equivalent in
`pyproject.toml`):

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest path/to/test_the_bug.py
```
