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
