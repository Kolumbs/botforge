# Quick start: chatting a bot into existence

A freshly deployed device knows nothing — no administrator, no LLM, no
personality, no tools. Everything below happens in conversation; nothing here
requires editing a file or restarting anything.

The example builds a help desk for a fictional bike shop. Substitute your own.

## Before you start

- A running botforge instance — see [example.toml](example.toml) and
  [README.md](README.md).
- A way to reach it: WebSocket, Slack, email, whatever zoozl is configured for.
- An API key for one of the supported providers.

## 1. First contact

The device has no LLM yet, so this exchange is not an agent — it is a plain
setup flow that runs before any model is reachable.

```
bot:  System is not configured yet. Please supply agent provider
      (e.g. openai, anthropic, gemini)
you:  openai
bot:  Provider registered. Please supply valid api-key of the provider.
you:  sk-...
bot:  Setup complete. Running openai on gpt-4o-mini. You can give me a
      personality, teach me tools, or change the model just by asking.
```

Whoever completes this becomes the administrator. If `admin_password` is set in
config, the device asks for that first.

From here on you are talking to the agent, and everything is a tool call.

## 2. Give it a personality

```
you:  You're the help desk for Cog & Sprocket, a bike shop. Be brief and
      friendly. If you don't know something, say so rather than guessing.
bot:  Instructions updated for 'main'.
```

## 3. Teach it something

Tools are Python. The source must define a `Params` model and an async
`handler`; see the contract in
[DEVELOPER.md](DEVELOPER.md#the-stored-tool-contract).

```
you:  Add a tool called opening_hours that tells people when we're open.

      import pydantic

      class Params(pydantic.BaseModel):
          day: str = pydantic.Field(default="", description="Day to check")

      async def handler(ctx, params) -> str:
          hours = {
              "saturday": "10:00-16:00",
              "sunday": "closed",
          }
          return hours.get(params.day.lower(), "09:00-18:00")

bot:  Tool 'opening_hours' defined successfully for agent 'main'.
```

It is callable on your next message — no restart.

```
you:  Are you open on Sunday?
bot:  No, we're closed on Sundays. We're open 9 to 6 on weekdays and
      10 to 4 on Saturdays.
```

## 4. Add a specialist

When one bot accumulates too many jobs, give the work its own agent. The main
bot delegates to it and decides when.

```
you:  Create an agent called repairs that handles servicing questions.
      It should be more technical and ask about the bike before advising.
bot:  Agent 'repairs' defined. 'main' can now delegate to it.

you:  Give repairs a tool called service_price for what a service costs.
      [...source, with agent="repairs"...]
bot:  Tool 'service_price' defined successfully for agent 'repairs'.
```

The specialist carries only its own tools — the administrative ones stay on
`main`, so a delegated call cannot reach them.

## 5. See what exists

```
you:  What can you do?
bot:  Defined tools:
        - opening_hours [main] (enabled): When the shop is open
        - service_price [repairs] (enabled): What a service costs

you:  Which agents are there?
bot:  Agents:
        - main (talks to people): opening_hours
        - repairs (called by main): service_price
```

## Changing your mind

| You want to | Ask for |
|---|---|
| Retire a tool but keep it | `disable_tool`, later `enable_tool` |
| Remove a tool for good | `delete_tool` |
| Remove an agent and its tools | `delete_agent` |
| Switch model or provider | `set_provider` |
| Hand admin to another device | `grant_admin` with that session's id |
| Start the LLM setup over | send `/setup` |

## Notes

- **The administrator can run code.** A tool is Python executed in the bot's
  process, unsandboxed. Anyone you grant admin to can do anything the process
  can. See the security notes in [README.md](README.md).
- **Tools are stored, not compiled.** The source stays readable and editable by
  chat; `define_tool` with an existing name replaces it.
- **A tool that needs a package which is not installed will be refused** at
  definition time, with the import error.
