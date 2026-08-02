# botforge — Implementation Complete

## What Was Built

A new, standalone repository (`/home/kolumbs/botforge`) implementing a chatbot platform where **bots are entirely database content**. No hardcoded per-bot logic or redeployment needed to add/modify capabilities.

### Core Idea

- **Traditional chatbot**: Tool definitions hardcoded in Python, deployed with `git push + restart`.
- **botforge**: Tool definitions stored in SQLite, deployed by chatting with `define_tool(...)`.

An admin can teach the bot new capabilities, change its personality, and fix bugs entirely through conversation.

## Architecture

### Three Layers

1. **zoozl** (external dependency) — Transport and message routing
   - Handles Slack, WebSocket, email, WhatsApp
   - No changes to zoozl; botforge uses it as-is
   - botforge does not use its `root.memory`; it opens its own database

2. **botforge** (new repo) — The bot engine
   - **tools.py** — `ToolSpec`, the framework-neutral tool descriptor (no SDK import)
   - **dynamic_tools.py** — Three dataclasses (BotConfig, DynamicTool, AdminGrant) + bootstrap tools + the tool contract (no SDK import)
   - **session.py** — Sliding-window conversation history over SQLite (no SDK import)
   - **openai_tools.py** — The one adapter: ToolSpec → `agents.FunctionTool`
   - **agent.py** — Loads DB state, falls back to "unconfigured" prompt, assembles Agent
   - **plugin.py** — zoozl Interface; per-message rebuilds agent to pick up tool/instruction changes

   Only the last three import the agent framework, so swapping it means rewriting one
   adapter plus two call sites.

3. **OpenAI Agents SDK** (external dependency) — The LLM loop
   - Handles tool calling, streaming, retry logic
   - botforge feeds it bootstrap + dynamic tools; everything else is SDK's responsibility

### Data Model

Three tables in SQLite (via membank):

```
BotConfig (singleton, id=1):
  - instructions: str (system prompt)
  - model: str (e.g., gpt-4o-mini)
  - updated_at: str

AdminGrant:
  - talker: str (PRIMARY KEY) — zoozl's session cookie
  - granted_at: str

DynamicTool:
  - name: str (PRIMARY KEY)
  - description: str
  - source_code: str (Python, must define Params + handler)
  - enabled: bool
  - created_by: str (talker)
  - updated_at: str
```

### Bootstrap Tools (Hardcoded, Always Available)

Six tools that can't themselves be dynamic (they need to write to the DB):

1. `claim_admin()` — First caller becomes admin (first-come, one-admin model)
2. `grant_admin(talker)` — Admin grants privileges to another session
3. `set_instructions(text, model?)` — Admin sets bot's system prompt
4. `define_tool(name, description, source_code)` — Admin adds a new tool (validates, executes, persists)
5. `list_tools()` — Show all tools (enabled/disabled)
6. `disable_tool(name)` — Disable a tool without deleting it

### Dynamic Tool Execution

When a tool is invoked:

1. Load its persisted source code
2. `exec()` it into a namespace (validates `Params` class + `handler` function exist)
3. Extract the `Params` (a pydantic model) and `handler` (an async function)
4. Call `handler(ctx, params)` where `ctx` has `{"memory": ..., "talker": ...}`

No sandboxing — admin code runs with full process privileges (intentional).

## What's Included

### Code

- **botforge/** — 7 Python modules
  - `__init__.py`, `tools.py`, `dynamic_tools.py`, `session.py`, `openai_tools.py`, `agent.py`, `plugin.py`
- **pyproject.toml** — Package config, deps (zoozl, openai-agents, pydantic)

### Documentation

- **README.md** — Overview, architecture, security notes
- **IMPLEMENTATION.md** — Detailed breakdown of each module and how to use it
- **QUICK_START.md** — Step-by-step guide to chat the profile bot into existence
- **SUMMARY.md** — This file

### Git History

See `git log` — the platform landed in one commit, followed by a decoupling pass
that made the tool/session layers framework-independent.

## How to Deploy

### Minimal Example

1. Create a config TOML (e.g., `profile_bot.toml`):

```toml
[botforge]
api_key = "sk-..."
aliases = ["bot", "help"]
database = "profile_bot.db"

[slack]
signing_secret = "..."
workspace_token = "..."
```

2. Start zoozl (which auto-loads botforge as a plugin):

```bash
python -m zoozl profile_bot.toml
```

3. In Slack (or whatever transport), talk to the bot:

```
@bot claim_admin
```

4. Chat the bot into existence (see QUICK_START.md for full dialogue):

```
@bot set_instructions

I'm your personal assistant...

@bot define_tool

name: get_contact
description: Returns Juris's email
source_code:
...
```

5. Test:

```
@bot What's your contact info?
```

Done. No code changes, no redeployment.

## Not Included (Out of Scope)

### Intentionally Not Built (Future Work)

- **Rust process supervisor** — Would manage Python interpreter, auto-install pip packages for new tools, enable clean restarts. Not needed for MVP since most tools don't need new dependencies.
- **Multi-tenant single-process** — Each bot is its own deployment today; future could be multiple bots in one zoozl process.
- **Migration/seed tools** — Scripts to import existing bot content (e.g., my_profile_chatbot → botforge). Deferred to after this passes validation.
- **Audit/permission model** — Currently just "one admin, forever." Could add revocation, multiple admins, role-based access later.

### Explicitly Out of Scope

- No changes to zoozl itself
- No FIFA extension migration (scope was "profile bot's CV tools only")
- No tallybot migration
- No voiceapi changes

## Validation Approach

Verified by hand rather than by a standing suite:

- The value layer (`tools`, `dynamic_tools`, `session`) imports with no LLM SDK present.
- `Bot` is a valid zoozl `Interface`.
- Admin bootstrap, the tool contract, session windowing and agent assembly were each
  exercised once during the decoupling pass; the bugs that surfaced are fixed.

**Still unverified:** a live end-to-end run against a real OpenAI key and a zoozl
config. That is the remaining gap before trusting a deployment — deploy, `claim_admin`,
and chat the bot into existence following QUICK_START.md.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **No shared secret for admin** | First caller wins. Simple, memorable, fits single-operator use case (you). |
| **Session-based admin (talker)** | Browser cookie persisted across restarts/conversation windows. No "re-auth per turn" needed. |
| **DB-backed, no in-memory state** | Full persistence. Process restart doesn't lose admin grants or tool definitions. |
| **In-process tool reload** | No process restart needed for pure-Python tool changes. Supervisor phase can handle pip installs later. |
| **No sandboxing for tool code** | Admin = code execution. You own the server and the bot. Intentional trade-off: simplicity over isolation. |
| **Live source code in DB** | Not bytecode or compiled blobs. Stays human-readable, editable by chat. |
| **botforge owns its own database** | zoozl's `root.memory` holds zoozl's conversation-routing state. A bot's tools, personality and grants are the product — different lifetime, different backup story — so botforge opens its own file rather than writing into the transport layer's. |
| **Separate deployments per bot** | Not a monolith. Each bot has its own process, config, database. Blast radius contained. |

## Testing

Tests are written by a dedicated testing persona/agent, never by the agent that
wrote the feature or fixed the bug under test, and are read-only to implementers.
See the testing policy in [DEVELOPER.md](DEVELOPER.md#testing-policy).

## File Manifest

```
botforge/
├── .gitignore
├── example.toml             # Reference config, with every default inline
├── pyproject.toml           # Package metadata, deps
├── README.md                # High-level overview
├── IMPLEMENTATION.md        # Detailed architecture
├── DEVELOPER.md             # Dev setup, layering rules, testing policy
├── QUICK_START.md           # Step-by-step usage guide
├── SUMMARY.md               # This file
└── botforge/
    ├── __init__.py          # Empty by design; zoozl loads botforge.plugin
    ├── tools.py             # ToolSpec descriptor        (no SDK)
    ├── dynamic_tools.py     # Dataclasses + bootstrap tools + contract  (no SDK)
    ├── session.py           # WindowedSession            (no SDK)
    ├── openai_tools.py      # ToolSpec → FunctionTool adapter
    ├── agent.py             # build_agent()
    └── plugin.py            # Bot(Interface) — zoozl wiring
```

## Next Steps

1. **Deploy this repo** alongside zoozl (add `extensions = ["botforge.plugin"]` to the zoozl config)
2. **Start the bot** and verify it loads
3. **Claim admin** via chat and teach it the profile bot's content (follow QUICK_START.md)
4. **Retire the old my_profile_chatbot** once this one is confirmed working
5. **Plan Phase 2** (Rust supervisor, multi-tenant, etc.) based on what you learn from live usage

---

**Status**: ✅ Implementation complete. Ready for deployment and validation.
