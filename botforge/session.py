"""Conversation history, stored in SQLite.

This module deliberately does not import the OpenAI Agents SDK. The SDK's
``Session`` contract is a structural protocol - any object exposing
``get_items``/``add_items``/``pop_item``/``clear_session`` plus ``session_id``
and ``session_settings`` satisfies it - so owning the storage here keeps
conversation persistence independent of whichever agent framework drives the
loop.
"""

import contextlib
import json
import sqlite3
import asyncio


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS conversation_items_session
    ON conversation_items (session_id, id);
"""


class WindowedSession:
    """Conversation history that replays only the last N items to the model.

    The full conversation is persisted, but each turn sends just a sliding
    window so the prompt does not grow without bound. The window is trimmed
    forward until it starts on a user message, so it never begins in the
    middle of an assistant/tool-call sequence (which the model rejects).
    """

    def __init__(self, session_id, db_path, window_size=10):
        self.session_id = session_id
        self.session_settings = None
        self.db_path = db_path
        self.window_size = window_size
        self._ensure_schema()

    @contextlib.contextmanager
    def _connect(self):
        """Yield a connection that commits on success and always closes."""
        conn = sqlite3.connect(self.db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_schema(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # Session protocol

    async def get_items(self, limit=None):
        """Return at most ``limit`` recent items, starting on a user turn."""
        if limit is None:
            limit = self.window_size
        payloads = await asyncio.to_thread(self._read_recent, limit)
        items = [json.loads(payload) for payload in payloads]
        while items and items[0].get("role") != "user":
            items.pop(0)
        return items

    async def add_items(self, items):
        """Append items to the stored conversation."""
        if not items:
            return
        await asyncio.to_thread(self._append, [json.dumps(item) for item in items])

    async def pop_item(self):
        """Remove and return the most recent item, or None when empty."""
        payload = await asyncio.to_thread(self._pop_last)
        return json.loads(payload) if payload is not None else None

    async def clear_session(self):
        """Remove every item belonging to this session."""
        await asyncio.to_thread(self._delete_all)

    # Blocking helpers, run off the event loop by the methods above

    def _read_recent(self, limit):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM conversation_items"
                " WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (self.session_id, limit),
            ).fetchall()
        return [row[0] for row in reversed(rows)]

    def _append(self, payloads):
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO conversation_items (session_id, payload) VALUES (?, ?)",
                [(self.session_id, payload) for payload in payloads],
            )

    def _pop_last(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, payload FROM conversation_items"
                " WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (self.session_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM conversation_items WHERE id = ?", (row[0],))
            return row[1]

    def _delete_all(self):
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM conversation_items WHERE session_id = ?",
                (self.session_id,),
            )
