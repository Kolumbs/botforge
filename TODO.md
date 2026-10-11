# TODO

## Admin on more than one channel

**Today:** admin belongs to one talker - the session that completed first-boot
setup. `grant_admin(talker)` can add another, but it needs that session's talker
ID, and each channel identifies people differently:

| Channel  | Talker ID                                    | Can the person find it?                  |
|----------|----------------------------------------------|------------------------------------------|
| Web page | random UUID in the `zoozl_talker` cookie     | No - the cookie is `HttpOnly`            |
| Slack    | Slack user ID (`U0…`)                        | Yes, from the Slack profile              |
| WhatsApp | phone number (`wa_id`)                       | Yes                                      |
| Email    | the *recipient* address (`message["to"]`)    | It does not identify the sender at all   |

So being admin in one place (a terminal client, a browser) does not carry over
to Slack, WhatsApp or another browser, and on the web there is no practical way
to grant it.

**Plan: link devices with a one-time code.**

1. An admin session asks the bot to link a device; a `link_device` tool returns
   a one-time code (e.g. `K7QM-3XRP`): 8 random characters, valid for 10
   minutes, single use.
2. From the new channel, the person sends `/link K7QM-3XRP`.
3. botforge grants admin to that session's talker and deletes the code.

`/link` is handled in `plugin.consume` before the agent, like `/setup`, so no
prompt can talk the model into granting admin. Pending codes are a small
dataclass in botforge's database.

**Alongside it:**

- `list_admins` and `revoke_admin`, to see which sessions are admin and remove a
  lost device or an old browser.
- Possibly `/whoami`, showing the session's own talker ID.

**Upstream (zoozl):** the email channel uses `message["to"]` as the talker, so
every sender shares one identity. That needs fixing in zoozl before email can
carry per-person admin.

## Keep API keys away from the model

**Today:** a key typed during first-boot setup, or after `/setup`, never reaches
the model - `plugin.consume` hands the message to `setup.advance` and returns
before `Runner.run`, so it is stored in `provider.api_key` and nowhere in the
history replayed to the LLM. Two gaps remain:

- **Keys given in chat go to the model.** Adding or changing a key through
  `set_provider` ("set my Anthropic key to sk-…") sends the key to the provider
  as prompt text and tool-call arguments, and keeps it in `conversation_items`,
  replayed for the next turns. Until this is fixed, change keys only with
  `/setup` - but `/setup` covers just the default provider, so adding a second
  provider's key has no safe path.
- **zoozl stores the setup transcript.** zoozl's `Conversation.messages` keeps
  every message, including the key typed during setup, in plain text in the same
  database. botforge never sends it to the model, and on the device the file is
  readable only by the service account and root, but it is a second copy of the
  key at rest.

**Plan:**

- A key-entry command handled before the agent, like `/setup` and `/link` -
  e.g. `/key anthropic`, after which the next message is taken as that
  provider's key and stored directly. `set_provider` stops accepting keys, so a
  key never becomes model input.
- Keep setup and key-entry messages out of zoozl's stored transcript, or redact
  them before it is saved; this may need a hook in zoozl.

**Minor:** when a provider rejects a key, its error text - with a masked key
such as `sk-inval****` - goes to the service log with the traceback.

## v1 multi-user: every user owns their own private assistant

**Decision:** anyone who connects gets their own bot, configures it with their
own provider keys, and is its only admin. Bots are private assistants for their
owner; nothing is shared between users. Publishing a bot to visitors (the
profile-bot case) comes later. No limits or payment for now - one Pi, few users,
full control to add them if usage grows.

This replaces the device-wide admin: there is no global setup or global admin
any more, only owners of their own bots. "Admin on more than one channel" above
becomes "the same user on more than one channel" (`/link`), and the API-key items
apply per user.

**Isolation is the core requirement.** Tools are arbitrary Python. Today they
run inside the botforge process as the `botforge` account, where one user's tool
could read every other user's database and keys, read other sessions from memory,
or crash every bot at once. Per-user folders alone do not help, since every
process of the same account can open the same files.

**Design:**

- **Router** - zoozl plus the botforge plugin, as today. It maps a talker to a
  user and forwards messages to that user's worker. zoozl's memory goes back to a
  file of its own, outside every user folder: it holds raw messages from all
  users, including keys typed in setup.
- **Worker per user** - a Python process per user, running that user's setup,
  agents and tools against `/var/lib/botforge/users/<id>/botforge.db`. Router and
  worker talk over pipes. A worker that crashes or hangs affects only its owner;
  the router restarts it. Idle workers stop after a while and start again on the
  next message, to save memory.
- **One Linux account per user, from a pool.** Each worker runs as its own account
  (`botforge-u001`, …) and its folder is that account's alone (`0700`), so the
  operating system - not botforge - keeps users out of each other's files and
  processes. Accounts cannot be created at runtime without root, so infra creates
  the pool (see infra-orchestrator TODO), and botforge assigns the next free
  account to a new user and records `talker → account` in the router's own
  database. The router starts a worker with
  `sudo -u botforge-uNNN /opt/botforge/current/worker uNNN` - the only sudo it has.
  When the pool is used up, new users are told there is no room until it grows.

**Alternatives considered:**

- *Worker per user under the same account* - isolates crashes and memory, not
  files.
- *Landlock* (a worker restricts itself to its folder, no root) - the lightest
  option, but not available on pi-server-1: kernel `6.1.21-v8+` runs only the
  `capability` security module. Worth revisiting after a kernel update.
- *bubblewrap* - works, but a sandbox per tool call was judged heavier than
  needed.

**What changes:**

- botforge: router/worker split; all per-bot data moves into the user's
  database; first-boot setup becomes per-user setup; the global admin tools go
  (`claim_admin`, `grant_admin`, the global `/setup`). The stored tool contract
  stays largely the same, since tools still run in-process - in their owner's
  worker.
- infra: the account pool and folders, one sudoers rule, and
  `NoNewPrivileges=false` for the botforge unit (it currently blocks all sudo).
- deploy: `deploy/start` runs the router; workers are started by the router from
  the current release.

**Exposure today:** the endpoint is reachable only by someone who knows to open a
websocket to `/ws/botforge`; nothing links to it. Until isolation exists, anyone
who finds it shares one bot, and its admin is whoever completed setup first.

## Next iteration: files and shared code libraries

Moving tallybot's accounting onto botforge showed what a serious bot needs that
botforge lacks. tallybot is a membank double-entry ledger with about 35
deterministic accounting commands, wrapped by one agent, and almost every flow
involves a file: an invoice PDF or photo in, a bank statement CSV in, an xlsx or
PDF report or a generated sales invoice out. Its logic is real code with
dependencies (`openpyxl`, `reportlab`, `pdftotext`), not prompts. tallybot is the
acceptance test for this iteration, not the iteration itself.

**Files**

- **In:** a tool can read the files that came with the message - name, media
  type, bytes - and pass one to the model when it should look at it (an invoice
  photo or PDF).
- **Out:** a tool can return a file, and botforge delivers it on the channel the
  message came from.
- **Kept:** a bot can store files in its own folder (e.g. invoice originals
  attached to bookings).
- **Check first:** zoozl's websocket protocol carries `{"text"}` only, while
  Slack and email carry attachments; files over websocket may need a zoozl
  change, as zoozl 0.3.0 was.
- This extends the stored tool contract (`ctx` gains attachments and a way to
  return files), so `CONTRACT_VERSION` moves.

**Shared code libraries** - reusable code many tools can import, so logic lives
in one tested place instead of being copied into every tool's source. Two kinds:

| | Platform libraries | Bot modules |
|---|---|---|
| What | Python packages installed with botforge, e.g. an extracted `tally` accounting package | modules written by chatting, stored in the bot's database, importable by that bot's tools |
| Written by | the operator, in a repository, reviewed and tested | the bot's owner, by chat |
| Shared with | every bot | that bot only |
| Dependencies | pinned in botforge's `uv.lock`, deployed by the pipeline | only what is already installed |
| For | correctness-critical code, like a ledger | glue and small helpers |

This fits the multi-user design: platform libraries are trusted code the
operator curates; bot modules and tools are owner code, running in the owner's
own worker and account.

**Order:**

1. Files - smallest, and useful to every bot at once.
2. Platform libraries, starting with extracting tallybot's data model and
   `brain/` into a plain package (no zoozl, no agents) that tallybot keeps using.
3. Bot modules.
4. tallybot's flows one at a time: read-only first (list transactions, ledger
   and outstanding reports, compared against tallybot), then single write flows
   (private expense, supplier invoice from a PDF, bank statement import, sales
   invoice), then the Slack channel, then retiring `tallybot.service`.

Multi-user v1 can come before or after; doing it first means files and bot
modules start out in the per-user folders.

**Open for tallybot:** whether the existing ledger is migrated into botforge's
database or botforge works on tallybot's database file.
