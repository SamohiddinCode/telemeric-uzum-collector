import tempfile
import unittest
from pathlib import Path

import httpx

from telegram_bot_sender import TelegramBotSender


class TelegramBotSenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_text_and_three_png_album(self):
        calls = []

        async def handler(request):
            calls.append((request.url.path, await request.aread()))
            return httpx.Response(200, json={"ok": True, "result": {"id": 8419189523}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sender = TelegramBotSender("test-token", client=client)
            with tempfile.TemporaryDirectory() as tmp:
                paths = []
                for index in range(3):
                    path = Path(tmp) / f"{index}.png"
                    path.write_bytes(b"fake-png")
                    paths.append(str(path))
                await sender.send_report(8419189523, paths, "<b>Test report</b>")

        self.assertEqual([path for path, _ in calls], [
            "/bottest-token/sendMessage",
            "/bottest-token/sendMediaGroup",
        ])
        self.assertIn(b"8419189523", calls[0][1])
        self.assertEqual(calls[1][1].count(b"fake-png"), 3)

    async def test_check_chat(self):
        async def handler(_request):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"id": 8419189523, "type": "private"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sender = TelegramBotSender("test-token", client=client)
            result = await sender.check_chat(8419189523)

        self.assertEqual(result, {"id": 8419189523, "type": "private", "username": None})

    async def test_send_message(self):
        requests = []

        async def handler(request):
            requests.append(await request.aread())
            return httpx.Response(200, json={"ok": True, "result": {}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sender = TelegramBotSender("test-token", client=client)
            await sender.send_message(8419189523, "<b>Connected</b>")

        self.assertEqual(len(requests), 1)
        self.assertIn(b"8419189523", requests[0])
        self.assertIn(b"Connected", requests[0])


if __name__ == "__main__":
    unittest.main()
