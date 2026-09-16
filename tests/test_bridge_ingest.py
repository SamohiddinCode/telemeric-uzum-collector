import unittest
from unittest.mock import AsyncMock, patch

import main
from fastapi import HTTPException


class BridgeIngestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_token = main.TELEGRAM_BOT_TOKEN
        main.TELEGRAM_BOT_TOKEN = "test-bot-token"

    def tearDown(self):
        main.TELEGRAM_BOT_TOKEN = self.previous_token

    async def test_rejects_invalid_token(self):
        with self.assertRaises(HTTPException) as context:
            await main.bridge_ingest({"updates": []}, authorization="Bearer wrong")
        self.assertEqual(context.exception.status_code, 401)

    async def test_forwards_updates_and_sets_source_identity(self):
        payload = {
            "updates": [{"update_id": 1, "message": {"message_id": 1}}],
            "account": "@uzum_franchise_support_bot",
            "groupTitle": "Uzum Franchise Chat",
        }
        deliver = AsyncMock()
        with patch.object(main, "deliver", deliver):
            result = await main.bridge_ingest(
                payload,
                authorization="Bearer test-bot-token",
            )
        self.assertEqual(result, {"ok": True, "accepted": 1})
        deliver.assert_awaited_once_with(payload["updates"])
        self.assertEqual(main.collector_status["account"], "@uzum_franchise_support_bot")
        self.assertEqual(main.collector_status["group"], "Uzum Franchise Chat")

    async def test_empty_batch_sends_heartbeat(self):
        site_request = AsyncMock(return_value={})
        with patch.object(main, "site_request", site_request):
            result = await main.bridge_ingest(
                {"updates": []},
                authorization="Bearer test-bot-token",
            )
        self.assertEqual(result, {"ok": True, "accepted": 0})
        site_request.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
