"""Sliding-window session management for conversation history."""

from agents import SQLiteSession


class WindowedSession(SQLiteSession):
    """SQLiteSession that only replays the last N items to the model.

    The full conversation is still persisted, but each turn sends just a
    sliding window so the prompt does not grow without bound. The window is
    trimmed forward until it starts on a user message, so it never begins in
    the middle of an assistant/tool-call sequence (which the model rejects).
    """

    def __init__(self, session_id, db_path, window_size=10):
        super().__init__(session_id=session_id, db_path=db_path)
        self.window_size = window_size

    async def get_items(self, limit: int | None = None):
        """Return at most ``window_size`` recent items, starting on a user turn."""
        if limit is None:
            limit = self.window_size
        items = await super().get_items(limit=limit)
        while items and items[0].get("role") != "user":
            items.pop(0)
        return items
