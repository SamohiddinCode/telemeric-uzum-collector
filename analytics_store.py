from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class MessageStore:
    """Small local cache used only for daily analytics.

    The Telegram -> Site delivery path remains the source of truth. This cache is
    deliberately disposable: the collector backfill repopulates it after a
    restart, so an ephemeral filesystem is acceptable for the daily report.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    ts INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL DEFAULT 0,
                    sender_name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    is_bot INTEGER NOT NULL DEFAULT 0,
                    sender_chat_id INTEGER NOT NULL DEFAULT 0,
                    text TEXT NOT NULL,
                    reply_to_message_id INTEGER,
                    PRIMARY KEY (chat_id, message_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telegram_messages_ts ON telegram_messages(chat_id, ts)"
            )

    def record_updates(self, updates: list[dict[str, Any]]) -> None:
        rows: list[tuple[Any, ...]] = []
        for update in updates:
            message = update.get("message") or {}
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            sender_chat = message.get("sender_chat") or {}
            sender_name = " ".join(
                part for part in [sender.get("first_name"), sender.get("last_name")] if part
            ).strip()
            rows.append(
                (
                    int(chat.get("id") or 0),
                    int(message.get("message_id") or 0),
                    int(message.get("date") or 0),
                    int(sender.get("id") or 0),
                    sender_name,
                    str(sender.get("username") or ""),
                    1 if sender.get("is_bot") else 0,
                    int(sender_chat.get("id") or 0),
                    text,
                    int((message.get("reply_to_message") or {}).get("message_id") or 0) or None,
                )
            )
        if not rows:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO telegram_messages (
                    chat_id, message_id, ts, sender_id, sender_name, username,
                    is_bot, sender_chat_id, text, reply_to_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    ts=excluded.ts,
                    sender_id=excluded.sender_id,
                    sender_name=excluded.sender_name,
                    username=excluded.username,
                    is_bot=excluded.is_bot,
                    sender_chat_id=excluded.sender_chat_id,
                    text=excluded.text,
                    reply_to_message_id=excluded.reply_to_message_id
                """,
                rows,
            )

    def get_messages(self, chat_id: int, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, message_id, ts, sender_id, sender_name, username,
                       is_bot, sender_chat_id, text, reply_to_message_id
                FROM telegram_messages
                WHERE chat_id = ? AND ts >= ? AND ts < ?
                ORDER BY ts ASC, message_id ASC
                """,
                (chat_id, start_ts, end_ts),
            ).fetchall()
        return [dict(row) for row in rows]
