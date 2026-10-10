# botforge

A chatbot platform where bot personality and capabilities are entirely database content, authored and updated by chatting with the bot. No code changes or redeployment needed to extend or modify a bot's behavior.

## Architecture

**botforge** ships as a generic, empty platform:

- Core modules handle the boilerplate: admin bootstrap, dynamic tool loading, agent assembly, session management.
- A bot's **instructions** (system prompt) and **tools** (function definitions) live entirely in SQLite as dataclass rows.
- Whoever completes first-boot setup is the admin, and teaches the bot new capabilities with `define_tool` (Python source code) and `set_instructions` (the bot's personality), all via conversation.

## Getting started

1. Install: `pip install -e .` (Python 3.11.1+; dependencies are pinned exactly in `pyproject.toml`).
2. Configure: copy [example.toml](example.toml) and edit it. Everything is optional — the example lists every setting with its default. For another language, copy `botforge/locales/en.toml`, translate it, and point `language` at it.
3. Run: `python -m zoozl --conf my_bot.toml` (the config needs `extensions = ["botforge.plugin"]`)
4. Chat: the device has no LLM yet, so it asks for a provider (`openai`, `anthropic`, `gemini`) and that provider's API key. The model is the provider's default and can be changed later just by asking.

From there the bot explains itself — it is seeded with a `guide` agent that
answers questions about giving it a personality, teaching it tools and adding
specialists. There is no setup guide in this repository because nobody
configuring a bot would be reading it.

## Project layout

```
botforge/
└── botforge/
    ├── __init__.py         # Empty by design - keeps the layers below SDK-free
    ├── tools.py            # ToolSpec: framework-neutral tool descriptor   ─┐
    ├── dynamic_tools.py    # Dataclasses, bootstrap tools, tool contract    ├─ no SDK
    ├── session.py          # Sliding-window conversation history            │
    ├── setup.py            # First-boot configuration, before any LLM      ─┘
    ├── openai_tools.py     # ToolSpec → agents.FunctionTool adapter        ─┐
    ├── agent.py            # build_agent(): assembles instructions + tools  ├─ framework
    ├── plugin.py           # Bot(Interface): zoozl wiring, the agent loop  ─┘
    └── locales/
        └── en.toml         # Everything the device says; copy to translate
```

Only the bottom three touch the agent framework, so swapping it means rewriting one
adapter plus two call sites.

## Bootstrap tools

Every bot instance ships with these built-in tools:

- `set_provider(provider?, api_key?, model?, default?)` — Add a provider with its API key, change a key or model, or make a provider the default; with no arguments it lists the stored providers (admin-only).
- `set_model(model, agent?)` — Run one agent on its own `provider/model`, e.g. `anthropic/claude-opus-5-5`; empty puts it back on the default provider (admin-only).
- `delete_provider(name)` — Forget a provider and its key; refused for the default or a provider an agent still runs on (admin-only).
- `claim_admin()` — Claim admin if nobody holds it yet. Setup normally grants it to whoever completes setup.
- `grant_admin(talker)` — Add another session ID as admin (admin-only).
- `set_instructions(text)` — Set the bot's system prompt (admin-only).
- `define_tool(name, description, source_code)` — Add a new tool by providing Python source (admin-only). Source must define a `Params` pydantic model and an async `handler` function.
- `define_agent(name, description, instructions, exposed_to?)` — Create a specialist agent to delegate to (admin-only).
- `list_tools()` / `list_agents()` — Show what is defined.
- `disable_tool(name)` / `enable_tool(name)` — Turn a tool off and on, keeping its source (admin-only).
- `delete_tool(name)` — Remove a tool and its source permanently (admin-only).
- `delete_agent(name)` — Remove an agent and every tool assigned to it (admin-only).

## How it works

1. **Persistence**: botforge opens its own SQLite database (`database` in config). Provider, BotConfig, DynamicTool and AdminGrant are membank dataclass tables; conversation history is an append-only table in the same file. zoozl's `root.memory` is left alone — that holds zoozl's own conversation-routing state.
2. **Bootstrap tools** (hardcoded in `dynamic_tools.py`) are always registered — they're the mechanism that lets everything else exist.
3. **Dynamic tools** (from DynamicTool rows) are loaded each turn and mixed into the agent's tool list. No process restart needed for pure-Python changes.
4. **Admin gate**: Every administrative tool checks if the current session's `talker` (a zoozl cookie-based session ID) is in the AdminGrant table. Whoever completes first-boot setup is granted admin forever — set `admin_password` in config on a public channel, otherwise the first person to reach the device gets it.
5. **Tool code execution**: When a dynamic tool is invoked, its stored Python `source_code` is `exec`'d into a namespace, the `Params` class and `handler` function are extracted, and the tool behaves like any other function tool.
6. **Failures get a reply**: a tool that raises reports the error to the model, and a turn that fails anyway answers with a line from the locale — the provider rejected the key, is unreachable, is busy, or something else went wrong — and logs the traceback.

## Security notes

- **No sandboxing**: Tool code runs with full process privileges. Admin access = code execution. This is intentional — you own the bot and the server.
- **Session identity via cookie**: `talker` is a long-lived browser cookie, not a real user account. Anyone with access to that cookie can act as that session.
- **No dependency management yet**: If a tool needs a pip package that isn't installed, `define_tool` will return an error. A future enhancement (the "Rust supervisor" phase) will handle auto-installation.

## Testing

Tests are written by a dedicated testing persona/agent, never by the agent that
wrote the feature or fixed the bug under test, and are read-only to implementers.
See the testing policy in [DEVELOPER.md](DEVELOPER.md#testing-policy).
