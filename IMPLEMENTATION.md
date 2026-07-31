# botforge Implementation

This document summarizes the implementation of the botforge platform and how to use it.

## Architecture Overview

botforge is a minimal chatbot platform where bot personality and capabilities are entirely database content. No source code changes are needed to add or modify a bot's behavior — everything is created and maintained through conversation.

### Core Components

1. **botforge/dynamic_tools.py** — Dataclasses for persistence + bootstrap tools
   - `BotConfig`: Singleton row with bot's instructions and model choice
   - `AdminGrant`: Tracks which session talkers have admin privileges
   - `DynamicTool`: Stores tool definitions (name, description, Python source code)
   - Bootstrap tools that can't themselves be dynamic (they're needed to write to the DB):
     - `claim_admin()`: First caller becomes admin
     - `grant_admin(talker)`: Admin grants privileges to another session
     - `set_instructions(text, model?)`: Admin sets bot's personality
     - `define_tool(name, description, source_code)`: Admin creates new tools
     - `list_tools()`, `disable_tool(name)`: Tool management

2. **botforge/openai_tools.py** — Adapter from framework-neutral ToolSpec to OpenAI Agents SDK
   - `ToolSpec` dataclass: name, description, Params pydantic model, handler function
   - `to_function_tools(specs, context)`: Converts ToolSpecs to `agents.FunctionTool` objects
   - Per-call context injection so tools can access `talker` (session identity) without the LLM passing it

3. **botforge/agent.py** — Agent assembly
   - `build_agent(conf, memory, model?)`: Loads BotConfig and DynamicTool rows, falls back to generic unconfigured prompt if none exist, assembles an Agent with bootstrap + dynamic tools
   - `UNCONFIGURED_PROMPT`: Generic fallback shown on a fresh database, explains how to use `claim_admin`, `define_tool`, `set_instructions`

4. **botforge/plugin.py** — zoozl Interface
   - `Bot` class: The zoozl-compatible chatbot plugin
   - `load(root)`: Reads config, stores `root` (for `root.memory`), builds initial agent
   - `consume(package)`: Per message, rebuilds agent (to pick up instruction/tool changes), runs `Runner.run(...)`, sends reply
   - Aliases read from config (`conf["botforge"]["aliases"]`), not hardcoded

5. **botforge/session.py** — Session management
   - `WindowedSession`: Extends Agents SDK's `SQLiteSession` to only replay the last N conversation items to the LLM (default 10), avoiding prompt bloat while keeping full history persisted

## How It Works

### Persistence Model

All state is persisted via `root.memory`, a `membank.LoadMemory` instance that zoozl creates automatically. It's a SQLite dataclass ORM, so every `@dataclass` in botforge (BotConfig, AdminGrant, DynamicTool) becomes a table:

```sql
CREATE TABLE bot_config (id INTEGER PRIMARY KEY, instructions TEXT, model TEXT, updated_at TEXT);
CREATE TABLE admin_grant (talker TEXT PRIMARY KEY, granted_at TEXT);
CREATE TABLE dynamic_tool (name TEXT PRIMARY KEY, description TEXT, source_code TEXT, enabled BOOLEAN, created_by TEXT, updated_at TEXT);
```

### Bootstrap Tools

Bootstrap tools are the only hardcoded Python functions in the system. They're always registered, and they're the mechanism that allows everything else to happen (a DB-defined tool can't be the thing that first writes to the DB):

1. **First user calls `claim_admin()`** — they're granted if the `AdminGrant` table is empty, otherwise they're told someone already claimed it.
2. **Admin calls `set_instructions(text, model?)`** — updates the singleton `BotConfig` row. On the next message, the agent will use the new instructions.
3. **Admin calls `define_tool(name, description, source_code)`** — `exec`s the source to validate it defines `Params` (a pydantic model) and `handler` (an async function). If valid, stores it in the `DynamicTool` table. On the next message, the new tool appears in the agent's tool list.
4. **Subsequent messages** — the agent rebuilds from BotConfig + all enabled DynamicTools, no restart needed.

### Admin Authorization

- **No shared secret** — whoever calls `claim_admin` first is permanently admin.
- **Session-based** — admin status is keyed by zoozl's `talker` (a long-lived browser cookie), so the same browser is admin forever (or until the database is deleted).
- **No sandboxing** — admin code runs with full process privileges (intentional — you own the bot).

### Dynamic Tool Execution

When an admin stores Python source code via `define_tool`:

```python
class Params(pydantic.BaseModel):
    message: str = pydantic.Field(description="What to echo")

async def handler(ctx, params) -> str:
    return f"You said: {params.message}"
```

The engine:
1. Validates the source (`exec` into a scratch namespace, checks `Params` and `handler` exist)
2. Persists the source in the `DynamicTool` row
3. At the next turn, loads and `exec`s all enabled tools into a namespace
4. Wraps each as a `ToolSpec` and converts to an `agents.FunctionTool`
5. Merges with bootstrap tools and passes to the Agent

## Configuration

Create a TOML file (e.g., `my_bot.toml`):

```toml
[botforge]
api_key = "sk-..."          # Required: OpenAI API key
aliases = ["bot", "help"]   # Optional: which aliases route to this bot
session_database = "my_bot_sessions.db"  # Optional: where to persist conversation history
history_window = 10         # Optional: how many messages to replay per turn

[slack]
signing_secret = "..."
workspace_token = "..."

[websocket]
port = 8000
```

Then run:

```bash
python -m zoozl my_bot.toml
```

zoozl will load botforge as a plugin (via `extensions` auto-discovery), create/open the SQLite database, and start the server. Connect via Slack, WebSocket, or email depending on config.

## Deployment

1. **First bot instance**: Create a new botforge deployment with a fresh database.
2. **Connect** to the bot (Slack, WebSocket, etc.).
3. **`claim_admin`**: Take ownership.
4. **`set_instructions`**: Tell the bot who/what it is.
5. **`define_tool` repeatedly**: Teach it capabilities.

Each bot is its own deployment with its own database file — they don't share a single monolithic database. This keeps blast radius contained per bot.

## Future Enhancements (not in this pass)

- **Rust process supervisor**: Manages the Python interpreter, auto-installs missing pip dependencies, cleanly restarts the process. Currently if a tool needs an uninstalled package, `define_tool` returns an error.
- **Multi-tenant single-process deployment**: Running multiple bots in one zoozl process (per-bot config rows, per-bot database shards). Currently each bot is a separate process.
- **Migration tools**: Scripts to seed a database with content from an existing bot (e.g., my_profile_chatbot → botforge).
- **Audit logging**: Track who created/modified each tool and when (currently `created_by` and `updated_at` are stored but not heavily used).

## Testing

Run `pytest tests/test_bootstrap.py` to verify the bootstrap mechanism. Tests require a mock membank since the real environment doesn't have all dependencies installed locally.

For integration testing with a real deployment, start the bot, call `claim_admin`, and teach it some tools via conversation.
