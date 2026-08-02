"""First-boot setup, driven without an LLM.

A device ships knowing nothing: no administrator, no API key, no model. That
leaves no way to run an agent, so the opening conversation is a plain state
machine instead. Each step is derived from what is already in the database
rather than from conversation state, so it survives restarts and a dropped
connection resumes where it left off.

Nothing here imports an LLM SDK - the whole point is that it works before one
can be used.
"""

from .dynamic_tools import (
    clear_provider,
    get_provider,
    grant_admin_talker,
    is_admin_talker,
    list_admin_talkers,
    save_provider,
)


CLAIM_WORD = "claim"
RESET_COMMAND = "/setup"
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-5",
    "gemini": "gemini-2.0-flash",
}


def is_configured(memory):
    """True once an agent can actually be run."""
    provider = get_provider(memory)
    return bool(provider and provider.api_key and provider.model)


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
        return (
            "This device is still being set up by its administrator. "
            "Try again shortly."
        )

    provider = get_provider(memory)

    if not provider or not provider.name or not provider.api_key:
        return _take_provider(memory, text)

    if not provider.model:
        return _take_model(memory, provider, text)

    return "Setup is already complete."


def _claim(memory, talker, text, admin_password):
    """Nobody administers this device yet."""
    expected = admin_password or CLAIM_WORD
    if text != expected:
        if admin_password:
            return (
                "This device has no administrator yet. "
                "Send the admin password to become one."
            )
        return (
            "This device has no administrator yet. "
            f"Reply '{CLAIM_WORD}' to become one - whoever does can run code on it."
        )

    grant_admin_talker(memory, talker)
    return (
        "You are the administrator now.\n\n"
        "Send the API key for the model you want me to use, in the form "
        "'<provider> <key>' - for example 'openai sk-...' or 'anthropic sk-ant-...'. "
        f"Known providers: {', '.join(sorted(DEFAULT_MODELS))} (others work if "
        "LiteLLM supports them)."
    )


def _take_provider(memory, text):
    """Expecting '<provider> <api key>'."""
    if not text:
        return (
            "Send the API key for the model you want me to use, as "
            "'<provider> <key>' - for example 'openai sk-...'. "
            f"Known providers: {', '.join(sorted(DEFAULT_MODELS))}."
        )

    parts = text.split(None, 1)
    if len(parts) != 2 or not parts[1].strip():
        return (
            "I need both the provider and the key, as '<provider> <key>' - "
            "for example 'openai sk-...'."
        )

    name, api_key = parts[0].lower(), parts[1].strip()
    save_provider(memory, name=name, api_key=api_key)

    suggested = DEFAULT_MODELS.get(name)
    if suggested:
        return (
            f"Key stored for {name}.\n\n"
            f"Which model should I use? Reply 'default' for {suggested}, "
            "or send a model name."
        )
    return f"Key stored for {name}.\n\nWhich model should I use? Send a model name."


def _take_model(memory, provider, text):
    """Expecting a model name, or 'default'."""
    if not text:
        return "Send a model name to finish setup."

    model = text
    if text.lower() == "default":
        model = DEFAULT_MODELS.get(provider.name, "")
        if not model:
            return (
                f"I have no default model for {provider.name}. "
                "Send a model name instead."
            )

    save_provider(memory, model=model)
    return (
        f"Setup complete - {provider.name}, {model}.\n\n"
        "I can talk now. As administrator you can give me a personality with "
        "set_instructions, teach me tools with define_tool, and change any of "
        f"this later with set_provider or by sending '{RESET_COMMAND}'."
    )
