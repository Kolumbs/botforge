"""Zoozl plugin: the generic botforge interface.

This Interface loads bots from botforge's own database and manages the agent
lifecycle. Tool and instruction changes are picked up on the next turn without
a process restart.

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

from .agent import build_agent
from .session import WindowedSession


log = logging.getLogger(__name__)

DEFAULT_DATABASE = "botforge.db"


class Bot(Interface):
    """Generic botforge interface: one agent, configuration and tools from database."""

    def load(self, root):
        """Open botforge's database, configure the API key, build the agent."""
        try:
            conf = root.conf["botforge"]
            api_key = conf["api_key"]
        except KeyError:
            raise RuntimeError(
                "botforge requires an 'api_key' in config [botforge] section"
            ) from None

        set_default_openai_key(api_key)

        self.conf = conf
        self.history_window = conf.get("history_window", 10)
        self.aliases = set(conf.get("aliases", ["bot", "help", "greet"]))

        # One file holds everything botforge owns: config, tools and history.
        self.database = os.path.abspath(conf.get("database", DEFAULT_DATABASE))
        self.memory = membank.LoadMemory(f"sqlite:///{self.database}")

        self.agent = build_agent(conf, self.memory)

    async def consume(self, package):
        """Handle an incoming message, rebuilding the agent so edits take effect."""
        package.conversation.subject = "bot"
        text = package.last_message_text

        if not text:
            package.callback(
                "Hello! I'm a botforge bot. If you're the admin, you can teach me "
                "new capabilities."
            )
            return

        # Rebuilt each turn so tool and instruction edits take effect without
        # restarting the process.
        self.agent = build_agent(self.conf, self.memory)

        result = await Runner.run(
            self.agent,
            text,
            session=WindowedSession(
                package.talker, self.database, self.history_window
            ),
            context=package,  # tools read package.talker via the run context
        )
        package.callback(result.final_output)
