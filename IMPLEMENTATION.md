# botforge Implementation

This document summarizes the implementation of the botforge platform and how to use it.

## Architecture Overview

botforge is a minimal chatbot platform where bot personality and capabilities are entirely database content. No source code changes are needed to add or modify a bot's behavior — everything is created and maintained through conversation.

### Core Components

1. **botforge/dynamic_tools.py** — Dataclasses for persistence + bootstrap tools
   - `BotConfig`: One row per agent — instructions, model, and `exposed_to` (which agent may delegate to it). The agent named `main` is the one people talk to.
   - `AdminGrant`: Tracks which session talkers have admin privileges
   - `DynamicTool`: Stores tool definitions (name, description, Python source code, and the `agent` that owns it)
   - Bootstrap tools that can't themselves be dynamic (they're needed to write to the DB):
     - `claim_admin()`: First caller becomes admin
     - `grant_admin(talker)`: Admin grants privileges to another session
     - `set_instructions(text, model?)`: Admin sets bot's personality
     - `define_tool(name, description, source_code, agent?)`: Admin creates new tools
     - `define_agent(name, description, instructions, exposed_to?)`: Admin creates a specialist the caller can delegate to
     - `list_tools()`, `list_agents()`: Inspection
     - `disable_tool(name)` / `enable_tool(name)`: Turn a tool off and on, keeping its source
     - `delete_tool(name)`: Remove a tool and its source for good
     - `delete_agent(name)`: Remove an agent and every tool assigned to it

2. **botforge/tools.py** — `ToolSpec`: the framework-neutral tool descriptor (name, description, Params pydantic model, handler). Imports no LLM SDK, so the tool layer stays independent of the agent framework.

3. **botforge/openai_tools.py** — The only adapter that binds ToolSpecs to the OpenAI Agents SDK
   - `to_function_tools(specs, context)`: Converts ToolSpecs to `agents.FunctionTool` objects
   - Per-call context injection so tools can access `talker` (session identity) without the LLM passing it
   - Swapping agent frameworks means writing a sibling of this file; nothing else changes

4. **botforge/agent.py** — Agent assembly
   - `build_agent(memory, conf)`: Loads BotConfig and DynamicTool rows, falls back to the generic unconfigured prompt if none exist, assembles an Agent with bootstrap + dynamic tools
   - `resolve_model(name, api_key)`: Bare name uses the SDK's OpenAI path; `provider/name` routes through LiteLLM, handed the same key
   - `UNCONFIGURED_PROMPT`: Generic fallback shown on a fresh database, explains how to use `claim_admin`, `define_tool`, `set_instructions`

5. **botforge/plugin.py** — zoozl Interface
   - `Bot` class: The zoozl-compatible chatbot plugin
   - `load(root)`: Reads config, opens botforge's own membank database, builds initial agent
   - `consume(package)`: Per message, rebuilds agent (to pick up instruction/tool changes), runs `Runner.run(...)`, sends reply
   - Aliases read from config (`conf["botforge"]["aliases"]`), not hardcoded

6. **botforge/session.py** — Conversation history
   - `WindowedSession`: SDK-free SQLite conversation store that replays only the last N items to the LLM (default 10), avoiding prompt bloat while keeping full history persisted

## How It Works

### Persistence Model

botforge opens its own `membank.LoadMemory` over the file named by `database` in config. It does **not** use zoozl's `root.memory`: that store belongs to zoozl and holds its conversation-routing state, whereas a bot's identity, tools and history are botforge's data with a different lifetime and backup story.

membank is a SQLite dataclass ORM, so each `@dataclass` becomes a table. Conversation history is log-shaped rather than dataclass-shaped, so `session.py` manages its own table in the same file:

```sql
CREATE TABLE botconfig   (name TEXT PRIMARY KEY, instructions TEXT, model TEXT, description TEXT, exposed_to TEXT, updated_at TEXT);
CREATE TABLE admingrant  (talker TEXT PRIMARY KEY, granted_at TEXT);
CREATE TABLE dynamictool (name TEXT PRIMARY KEY, agent TEXT, description TEXT, source_code TEXT, enabled BOOLEAN, created_by TEXT, updated_at TEXT, contract_version INTEGER);
CREATE TABLE conversation_items (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, payload TEXT);
```

### Bootstrap Tools

Bootstrap tools are the only hardcoded Python functions in the system. They're always registered, and they're the mechanism that allows everything else to happen (a DB-defined tool can't be the thing that first writes to the DB):

1. **First user calls `claim_admin()`** — they're granted if the `admingrant` table is empty, otherwise they're told someone already claimed it.
2. **Admin calls `set_instructions(text, model?)`** — updates the singleton `BotConfig` row. On the next message, the agent will use the new instructions.
3. **Admin calls `define_tool(name, description, source_code)`** — `exec`s the source to validate it defines `Params` (a pydantic model) and `handler` (an async function). If valid, stores it in the `DynamicTool` table. On the next message, the new tool appears in the agent's tool list.
4. **Subsequent messages** — the agent rebuilds from BotConfig + all enabled DynamicTools, no restart needed.

### Delegation

`main` is the agent a person talks to. `define_agent` creates a specialist and
names which agent may delegate to it; `build_agent` attaches each specialist to
its parent via the SDK's `Agent.as_tool()`, recursively. Only `main` carries the
bootstrap tools — a specialist gets just the tools assigned to it with
`define_tool(agent=...)`, so the admin surface is not reachable from inside a
delegated call. Delegation cycles are refused at definition time and again when
building, so a loop logs a warning instead of recursing forever.

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
1. Validates the source against the contract in `TOOL_CONTRACT` — `exec` into a scratch
   namespace, then check `Params` is a pydantic model and `handler` is an async function
   taking exactly two positional arguments. `load_tool_source()` is the single place this
   is enforced, used by both the write path and the load path, so an accepted tool always
   loads again later.
2. Persists the source in the `DynamicTool` row, stamped with `CONTRACT_VERSION`
3. At the next turn, loads and `exec`s all enabled tools into a namespace
4. Wraps each as a `ToolSpec` and converts to an `agents.FunctionTool`
5. Merges with bootstrap tools and passes to the Agent

## Configuration

Create a TOML file (e.g., `my_bot.toml`):

```toml
[botforge]
api_key = "sk-..."          # Required: credential for whichever provider you use
model = "gpt-4o-mini"       # Optional: bare name = OpenAI, "provider/name" = LiteLLM
aliases = ["bot", "help"]   # Optional: which aliases route to this bot
database = "my_bot.db"      # Optional: botforge's own database (default botforge.db)
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

zoozl imports each module named in `extensions`, discovers the `Interface` subclass in it, and starts the server. Point it at the plugin module, not the package:

```toml
extensions = ["botforge.plugin"]
# memory_path is zoozl's own store for conversation routing. It is separate
# from botforge's `database` above, and optional - unset means zoozl keeps
# that state in memory only.
memory_path = "sqlite://zoozl_routing.db"
```

Connect via Slack, WebSocket, or email depending on config.

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

Tests are written by a dedicated testing persona/agent, never by the agent that
wrote the feature or fixed the bug under test, and are read-only to implementers.
See the testing policy in [DEVELOPER.md](DEVELOPER.md#testing-policy).
