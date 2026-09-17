from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx


class TelegramBotSender:
    """Send reports with the existing service bot via Telegram Bot API."""

    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._client = client

    async def _request(self, method: str, **kwargs: Any) -> dict[str, Any]:
        if self._client is not None:
            response = await self._client.post(f"{self.base_url}/{method}", **kwargs)
        else:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(f"{self.base_url}/{method}", **kwargs)
        payload = response.json()
        if not response.is_success or not payload.get("ok"):
            description = payload.get("description") or f"HTTP {response.status_code}"
            raise RuntimeError(f"Telegram {method} failed: {description}")
        return payload

    async def check_chat(self, chat_id: int) -> dict[str, Any]:
        payload = await self._request("getChat", json={"chat_id": chat_id})
        chat = payload.get("result") or {}
        return {
            "id": chat.get("id"),
            "type": chat.get("type"),
            "username": chat.get("username"),
        }

    async def send_report(self, chat_id: int, paths: list[str], text: str) -> None:
        await self._request(
            "sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )
        media = [
            {"type": "photo", "media": f"attach://photo{index}"}
            for index in range(len(paths))
        ]
        with ExitStack() as stack:
            files = {
                f"photo{index}": (
                    Path(path).name,
                    stack.enter_context(open(path, "rb")),
                    "image/png",
                )
                for index, path in enumerate(paths)
            }
            await self._request(
                "sendMediaGroup",
                data={"chat_id": str(chat_id), "media": json.dumps(media)},
                files=files,
            )
