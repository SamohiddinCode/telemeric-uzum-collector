import json
import unittest
from datetime import date

import httpx

from analytics_config import ReportConfig
from google_sheets_writer import GoogleSheetsWriter


class GoogleSheetsWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_complete_daily_report_payload(self):
        captured = {}

        async def handler(request):
            captured.update(json.loads(await request.aread()))
            return httpx.Response(200, json={"ok": True, "row": 2})

        config = ReportConfig(-1002707306458, -5271279516, sla_target_minutes=15)
        metrics = {
            "shift_start": "10:00",
            "shift_end": "19:00",
            "shift_source": "configured",
            "total_tickets": 18,
            "responded": 15,
            "sla_ok": 13,
            "sla_percent": 72.2222,
            "median_minutes": 4.35,
            "faq_coverage_percent": 11.1111,
            "unanswered": 3,
            "sla_breaches": 5,
            "delays": [{"minutes": 20}, {"minutes": 31}],
            "support_reply_messages": 16,
            "linked_support_replies": 15,
            "unlinked_reply_targets": 1,
            "root_causes": [{"label": "Другое", "count": 16, "percent": 88.9}],
            "excluded": {"partner_dialogue": 27, "non_inquiry": 22, "automated": 0},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            writer = GoogleSheetsWriter("https://example.test/webhook", "secret", client=client)
            result = await writer.upsert_day(
                date(2026, 9, 22),
                metrics,
                config,
                "Комментарий отчёта",
                -5271279516,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(captured["secret"], "secret")
        report = captured["report"]
        self.assertEqual(report["date"], "2026-09-22")
        self.assertEqual(report["tickets"], 18)
        self.assertEqual(report["slaCompliant"], 13)
        self.assertEqual(report["slaTargetMinutes"], 15)
        self.assertEqual(report["delayedReplies"], 2)
        self.assertEqual(report["excluded"]["partner_dialogue"], 27)
        self.assertEqual(report["reportChatId"], -5271279516)

    async def test_rejects_unsuccessful_webhook_response(self):
        async def handler(request):
            return httpx.Response(200, json={"ok": False, "error": "unauthorized"})

        config = ReportConfig(-1002707306458, -5271279516)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            writer = GoogleSheetsWriter("https://example.test/webhook", "bad", client=client)
            with self.assertRaises(RuntimeError):
                await writer.upsert_day(date(2026, 9, 22), {}, config, "", -5271279516)
