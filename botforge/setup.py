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
"""

from .dynamic_tools import (
    PROVIDERS,
    clear_provider,
    get_provider,
    grant_admin_talker,
    is_admin_talker,
    list_admin_talkers,
    save_provider,
)


RESET_COMMAND = "/setup"

ASK_PROVIDER = "Please supply agent provider (e.g. " + ", ".join(PROVIDERS) + ")"
ASK_KEY = "Please supply valid api-key of the provider."
NOT_CONFIGURED = "System is not configured yet."


def is_configured(memory):
    """True once an agent can actually be run."""
    provider = get_provider(memory)
    return bool(provider and provider.name and provider.api_key)


def reset(memory):
    """Forget the provider so setup runs again. Administrators are kept."""
    clear_provider(memory)


def advance(memory, talker, text, admin_password=""):
    """Take one step of setup and return the reply to send.

    Called for every message until ``is_configured`` is true.
    """
    text = (text or "").strip()

    if not list_admin_talkers(memory):
        return _claim(memory, talker, text, admin_password)

    if not is_admin_talker(memory, talker):
        return "This device is still being set up by its administrator."

    provider = get_provider(memory)

    if not provider or not provider.name:
        return _take_provider(memory, text)

    if not provider.api_key:
        return _take_key(memory, provider, text)

    return "Setup is already complete."


def _claim(memory, talker, text, admin_password):
    """Nobody administers this device yet."""
    if admin_password:
        if text != admin_password:
            return f"{NOT_CONFIGURED} Please supply the admin password."
        grant_admin_talker(memory, talker)
        return ASK_PROVIDER

    # With no password set, whoever reaches the device first administers it.
    grant_admin_talker(memory, talker)
    return f"{NOT_CONFIGURED} {ASK_PROVIDER}"


def _take_provider(memory, text):
    """Expecting one of the known provider names."""
    if not text:
        return f"{NOT_CONFIGURED} {ASK_PROVIDER}"

    name = text.lower()
    if name not in PROVIDERS:
        return f"'{text}' is not correct provider. {ASK_PROVIDER}"

    save_provider(memory, name=name, model=PROVIDERS[name]["model"])
    return f"Provider registered. {ASK_KEY}"


def _take_key(memory, provider, text):
    """Expecting the provider's API key."""
    if not text or len(text.split()) > 1:
        return ASK_KEY

    save_provider(memory, api_key=text)
    return (
        f"Setup complete. Running {provider.name} on {provider.model}. "
        "You can give me a personality, teach me tools, or change the model "
        "just by asking."
    )
