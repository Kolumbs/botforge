"""Zoozl plugin: the generic botforge interface.

This Interface loads bots from the database and manages the agent lifecycle.
Tool/instruction changes are picked up on the next turn without a process restart.
"""

import logging

from agents import Runner, set_default_openai_key
from zoozl.chatbot import Interface

from .agent import build_agent
from .session import WindowedSession


log = logging.getLogger(__name__)


def _resolve_session_db(conf, root):
    """Pick the SQLite file conversation history is written to.

    Defaults to the same file zoozl already opened for ``root.memory``, so
    history sits beside the BotConfig/DynamicTool/AdminGrant rows instead of in
    a second database. An explicit ``session_database`` in config wins, and an
    in-memory zoozl store falls back to a file so history survives a restart.
    """
    explicit = conf.get("session_database")
    if explicit:
        return explicit

    memory_path = root.conf.get("memory_path", "") or ""
    if isinstance(memory_path, str) and memory_path.startswith("sqlite://"):
        path = memory_path[len("sqlite://") :]
        if path and path != ":memory:":
            return path

    return "botforge_sessions.db"


class Bot(Interface):
    """Generic botforge interface: one agent, configuration and tools from database."""

    def load(self, root):
        """Configure the OpenAI key, build the initial agent, and store config."""
        try:
            conf = root.conf["botforge"]
            api_key = conf["api_key"]
        except KeyError:
            raise RuntimeError(
                "botforge requires an 'api_key' in config [botforge] section"
            ) from None

        set_default_openai_key(api_key)

        self.root = root
        self.conf = conf
        self.session_db = _resolve_session_db(conf, root)
        self.history_window = conf.get("history_window", 10)
        self.aliases = set(conf.get("aliases", ["bot", "help", "greet"]))

        # Build the initial agent
        self.agent = build_agent(conf, root.memory)
        self.tools_version = 0

    async def consume(self, package):
        """Handle an incoming message, rebuilding the agent if tools/instructions changed."""
        package.conversation.subject = "bot"
        text = package.last_message_text

        if not text:
            # Initial greeting on connect
            package.callback(
                "Hello! I'm a botforge bot. If you're the admin, you can teach me new capabilities."
            )
            return

        # Check if tools or instructions have changed (by rebuilding and comparing)
        # For now, we rebuild every turn to pick up changes. In production, this could be optimized.
        old_agent = self.agent
        self.agent = build_agent(self.conf, self.root.memory)

        # Run the agent
        result = await Runner.run(
            self.agent,
            text,
            session=WindowedSession(
                package.talker, self.session_db, self.history_window
            ),
            context=package,  # tools read package.talker via the run context
        )
        package.callback(result.final_output)
