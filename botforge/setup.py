"""First-boot setup, driven without an LLM.

A device ships knowing nothing: no administrator, no provider, no key. That
leaves no way to run an agent, so the opening exchange is a plain state machine
instead - pick a provider, supply its key, done. The model is whatever that
provider defaults to; an administrator can change it later through the agent
itself with set_provider.

Each step is derived from what is already in the database rather than from
conversation state, so a restart or a dropped connection resumes where it left
off. Nothing here imports an LLM SDK - the whole point is that it works before
one can be used.

Every line the device says here is in MESSAGES and can be replaced from the
``[botforge.messages]`` config section.
"""

import logging

from .dynamic_tools import (
    PROVIDERS,
    clear_provider,
    get_provider,
    grant_admin_talker,
    is_admin_talker,
    list_admin_talkers,
    save_provider,
)


log = logging.getLogger(__name__)

RESET_COMMAND = "/setup"

# Each message stands on its own rather than being composed from the others,
# so editing one in config cannot surprise you by changing another. Available
# placeholders are named per entry.
MESSAGES = {
    # {providers}
    "ask_provider": (
        "System is not configured yet. "
        "Please supply agent provider (e.g. {providers})"
    ),
    # {value}, {providers}
    "bad_provider": (
        "'{value}' is not correct provider. "
        "Please supply agent provider (e.g. {providers})"
    ),
    "ask_password": "System is not configured yet. Please supply the admin password.",
    "provider_registered": "Provider registered. Please supply valid api-key of the provider.",
    "ask_key": "Please supply valid api-key of the provider.",
    "setup_in_progress": "This device is still being set up by its administrator.",
    # {provider}, {model}
    "setup_complete": (
        "Setup complete. Running {provider} on {model}. You can give me a "
        "personality, teach me tools, or change the model just by asking."
    ),
    "already_configured": "Setup is already complete.",
    # Said on connect once the device is configured, before anyone speaks.
    "greeting": (
        "Hello! I'm a bot on the kolumbs.net platform. If you're the admin, "
        "you can teach me new capabilities."
    ),
}


def say(messages, key, **values):
    """Render one message, falling back to the built-in on a bad override.

    Setup is the only way into a device, so a mistyped placeholder in config
    must not be able to make it unusable.
    """
    values.setdefault("providers", ", ".join(PROVIDERS))
    template = (messages or {}).get(key, MESSAGES[key])
    try:
        return template.format(**values)
    except (KeyError, IndexError) as error:
        log.warning(
            "Ignoring configured message %r - it uses %s, which is not available here.",
            key,
            error,
        )
        return MESSAGES[key].format(**values)


def is_configured(memory):
    """True once an agent can actually be run."""
    provider = get_provider(memory)
    return bool(provider and provider.name and provider.api_key)


def reset(memory):
    """Forget the provider so setup runs again. Administrators are kept."""
    clear_provider(memory)


def advance(memory, talker, text, admin_password="", messages=None):
    """Take one step of setup and return the reply to send.

    Called for every message until ``is_configured`` is true.
    """
    text = (text or "").strip()

    if not list_admin_talkers(memory):
        return _claim(memory, talker, text, admin_password, messages)

    if not is_admin_talker(memory, talker):
        return say(messages, "setup_in_progress")

    provider = get_provider(memory)

    if not provider or not provider.name:
        return _take_provider(memory, text, messages)

    if not provider.api_key:
        return _take_key(memory, provider, text, messages)

    return say(messages, "already_configured")


def _claim(memory, talker, text, admin_password, messages):
    """Nobody administers this device yet."""
    if admin_password and text != admin_password:
        return say(messages, "ask_password")

    # With no password set, whoever reaches the device first administers it.
    grant_admin_talker(memory, talker)
    return say(messages, "ask_provider")


def _take_provider(memory, text, messages):
    """Expecting one of the known provider names."""
    if not text:
        return say(messages, "ask_provider")

    name = text.lower()
    if name not in PROVIDERS:
        return say(messages, "bad_provider", value=text)

    save_provider(memory, name=name, model=PROVIDERS[name]["model"])
    return say(messages, "provider_registered")


def _take_key(memory, provider, text, messages):
    """Expecting the provider's API key."""
    if not text or len(text.split()) > 1:
        return say(messages, "ask_key")

    save_provider(memory, api_key=text)
    return say(messages, "setup_complete", provider=provider.name, model=provider.model)
