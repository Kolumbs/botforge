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

No English lives here. Every line the device says comes from a locale file -
``locales/en.toml`` by default - so a device can be built for another language
by translating that file and pointing ``language`` at it.
"""

import logging
import pathlib
import tomllib

from .dynamic_tools import (
    PROVIDERS,
    ROOT_AGENT,
    clear_provider,
    get_provider,
    grant_admin_talker,
    is_admin_talker,
    list_admin_talkers,
    save_provider,
    seed_agent,
)


log = logging.getLogger(__name__)

RESET_COMMAND = "/setup"

GUIDE_AGENT = "guide"

# The names of the lines a device needs, not their text. A locale file must
# supply all of them; anything missing is caught when it is loaded rather than
# part-way through a conversation.
REQUIRED_MESSAGES = frozenset(
    {
        "ask_provider",
        "bad_provider",
        "ask_password",
        "provider_registered",
        "ask_key",
        "setup_in_progress",
        "setup_complete",
        "already_configured",
    }
)

LOCALES = pathlib.Path(__file__).parent / "locales"
DEFAULT_LANGUAGE = "en"


def load_locale(language=DEFAULT_LANGUAGE):
    """Load a language's text: its unconfigured prompt and its messages.

    ``language`` is either the name of a bundled locale (``en``) or a path to
    a TOML file of the same shape, which is how a device gets a language
    botforge does not ship.
    """
    path = pathlib.Path(language)
    if path.suffix != ".toml" or not path.is_file():
        path = LOCALES / f"{language}.toml"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in LOCALES.glob("*.toml")))
        raise RuntimeError(
            f"No locale {language!r}. Bundled: {available}. "
            "Or give the path to a .toml file of the same shape."
        )

    with open(path, "rb") as handle:
        locale = tomllib.load(handle)

    missing = REQUIRED_MESSAGES - set(locale.get("messages") or {})
    if missing:
        raise RuntimeError(
            f"Locale {path} is missing messages: {', '.join(sorted(missing))}"
        )
    return locale


def say(messages, key, **values):
    """Render one message from the loaded locale.

    A placeholder the message cannot be given is left as written rather than
    raising - setup is the only way into a device, so a typo in a translation
    should degrade the wording, not block the exchange.
    """
    values.setdefault("providers", ", ".join(PROVIDERS))
    template = messages[key]
    try:
        return template.format(**values)
    except (KeyError, IndexError) as error:
        log.warning("Message %r uses %s, which is not available here.", key, error)
        return template


def is_configured(memory):
    """True once an agent can actually be run."""
    provider = get_provider(memory)
    return bool(provider and provider.name and provider.api_key)


def reset(memory):
    """Forget the provider so setup runs again. Administrators are kept."""
    clear_provider(memory)


def advance(memory, talker, text, messages, admin_password="", locale=None):
    """Take one step of setup and return the reply to send.

    ``messages`` comes from ``load_locale``; there is no default, because the
    device has no words of its own.
    """
    text = (text or "").strip()

    if not list_admin_talkers(memory):
        return _claim(memory, talker, text, messages, admin_password)

    if not is_admin_talker(memory, talker):
        return say(messages, "setup_in_progress")

    provider = get_provider(memory)

    if not provider or not provider.name:
        return _take_provider(memory, text, messages)

    if not provider.api_key:
        return _take_key(memory, provider, text, messages, locale)

    return say(messages, "already_configured")


def _claim(memory, talker, text, messages, admin_password):
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

    save_provider(memory, name=name)
    return say(messages, "provider_registered")


def _take_key(memory, provider, text, messages, locale=None):
    """Expecting the provider's API key. The last step, so seed the guide."""
    if not text or len(text.split()) > 1:
        return say(messages, "ask_key")

    save_provider(memory, api_key=text)
    _seed_guide(memory, locale)
    return say(messages, "setup_complete", provider=provider.name, model=provider.model)


def _seed_guide(memory, locale):
    """Give the device a specialist that explains how it works.

    Seeded only on a device that has no agents at all, so it appears once when
    a device first becomes usable. After that it is an ordinary agent: editable
    with set_instructions, removable with delete_agent, and deleting it sticks
    even if setup is run again.
    """
    guide = (locale or {}).get("guide")
    if not guide or list(memory.get("botconfig")):
        return  # this device has agents already, so it is not a fresh one
    seed_agent(
        memory,
        name=GUIDE_AGENT,
        description=guide["description"],
        instructions=guide["instructions"],
        exposed_to=ROOT_AGENT,
    )
