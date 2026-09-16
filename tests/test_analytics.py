import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics_config import ReportConfig
from analytics_engine import build_metrics
from report_renderer import ReportRenderer


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.config = ReportConfig(-1002707306458, -1002707306458, agent_ids={900})
        tz = ZoneInfo("Asia/Tashkent")
        ts = lambda h, m: int(datetime(2026, 9, 16, h, m, tzinfo=tz).timestamp())
        self.messages = [
            {"message_id": 1, "ts": ts(10, 1), "sender_id": 100, "sender_name": "Partner A", "username": "a", "text": "Когда будет выплата?", "reply_to_message_id": None},
            {"message_id": 2, "ts": ts(10, 3), "sender_id": 100, "sender_name": "Partner A", "username": "a", "text": "Проверьте пожалуйста", "reply_to_message_id": None},
            {"message_id": 3, "ts": ts(10, 8), "sender_id": 900, "sender_name": "Support", "username": "support", "text": "Выплата сегодня после 15:00", "reply_to_message_id": 1},
            {"message_id": 4, "ts": ts(11, 0), "sender_id": 101, "sender_name": "Partner B", "username": "b", "text": "Приложение не работает", "reply_to_message_id": None},
            {"message_id": 5, "ts": ts(11, 30), "sender_id": 900, "sender_name": "Support", "username": "support", "text": "Перезапустите приложение", "reply_to_message_id": 4},
            {"message_id": 6, "ts": ts(12, 0), "sender_id": 102, "sender_name": "Partner C", "username": "c", "text": "Нужен договор аренды", "reply_to_message_id": None},
        ]

    def test_metrics(self):
        metrics = build_metrics(self.messages, self.config)
        self.assertEqual(
            (metrics["total_tickets"], metrics["responded"], metrics["unanswered"], metrics["sla_ok"]),
            (3, 2, 1, 1),
        )
        self.assertAlmostEqual(metrics["sla_percent"], 50.0)
        self.assertAlmostEqual(metrics["median_minutes"], 18.5)

    def test_render(self):
        metrics = build_metrics(self.messages, self.config)
        renderer = ReportRenderer()
        with tempfile.TemporaryDirectory() as tmp:
            paths = [str(Path(tmp) / name) for name in ["a.png", "b.png", "c.png"]]
            renderer.summary(metrics, self.config, date(2026, 9, 16), paths[0])
            renderer.charts(metrics, self.config, date(2026, 9, 16), paths[1])
            renderer.details(metrics, self.config, date(2026, 9, 16), paths[2])
            for path in paths:
                self.assertGreater(Path(path).stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
