"""Zoozl plugin: the generic botforge interface.

Almost nothing is configured in files. A device boots knowing only where to put
its database; the administrator, the LLM, and every agent and tool are set up
by talking to it. Until an LLM is configured there is nothing to run an agent
with, so messages go to ``setup``, a plain state machine, instead.

botforge does not use zoozl's ``root.memory``. That store belongs to zoozl and
holds its conversation-routing state; a bot's identity, tools and history are
botforge's own data with a different lifetime and a different backup story, so
botforge opens its own database from its own config.
"""

import logging
import os

import membank
from agents import Runner, set_default_openai_key
from zoozl.chatbot import Interface

from . import setup
from .agent import build_agent
from .dynamic_tools import PROVIDERS, get_provider, is_admin_talker
from .session import WindowedSession


log = logging.getLogger(__name__)

DEFAULT_DATABASE = "botforge.db"


class Bot(Interface):
    """Generic botforge interface: one agent, configuration and tools from database."""

    def load(self, root):
        """Open botforge's database. Everything else is set up by conversation."""
        conf = root.conf.get("botforge", {})

        self.history_window = conf.get("history_window", 10)
        self.aliases = set(conf.get("aliases", ["bot", "help", "greet"]))
        # Optional. Without it, the first person to reach the device can claim
        # it - fine on a private channel, less so on a public one.
        self.admin_password = conf.get("admin_password", "")
        # What a bot says before an administrator gives it a personality.
        self.unconfigured_prompt = conf.get("unconfigured_prompt", "")

        # One file holds everything botforge owns: config, tools and history.
        self.database = os.path.abspath(conf.get("database", DEFAULT_DATABASE))
        self.memory = membank.LoadMemory(f"sqlite:///{self.database}")
        self.applied_key = None

    def apply_provider_key(self):
        """Hand the SDK the stored key, once per change rather than per turn."""
        provider = get_provider(self.memory)
        if PROVIDERS.get(provider.name, {}).get("litellm_prefix", provider.name):
            return  # LiteLLM providers get the key when their model is built
        if provider.api_key != self.applied_key:
            set_default_openai_key(provider.api_key)
            self.applied_key = provider.api_key

    async def consume(self, package):
        """Run setup until the device is configured, then hand over to the agent."""
        package.conversation.subject = "bot"
        text = package.last_message_text
        talker = package.talker

        if (text or "").strip().lower() == setup.RESET_COMMAND and is_admin_talker(
            self.memory, talker
        ):
            setup.reset(self.memory)
            self.applied_key = None
            package.callback(setup.advance(self.memory, talker, "", self.admin_password))
            return

        if not setup.is_configured(self.memory):
            package.callback(
                setup.advance(self.memory, talker, text, self.admin_password)
            )
            return

        if not text:
            package.callback(
                "Hello! I'm a bot on the kolumbs.net platform. If you're the admin, "
                "you can teach me new capabilities."
            )
            return

        self.apply_provider_key()

        # Rebuilt each turn so tool and instruction edits take effect without
        # restarting the process.
        self.agent = build_agent(self.memory, unconfigured_prompt=self.unconfigured_prompt)

        result = await Runner.run(
            self.agent,
            text,
            session=WindowedSession(
                package.talker, self.database, self.history_window
            ),
            context=package,  # tools read package.talker via the run context
        )
        package.callback(result.final_output)
