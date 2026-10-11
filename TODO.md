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
