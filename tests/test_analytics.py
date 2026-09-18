import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from analytics_config import ReportConfig
from analytics_engine import build_metrics
from analytics_store import MessageStore
from daily_report import DailyReportService
from report_renderer import ReportRenderer


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.config = ReportConfig(
            -1002707306458,
            8419189523,
            agent_usernames={"uzum_franchise"},
            service_bot_usernames={"opening_closing_bot"},
        )
        tz = ZoneInfo("Asia/Tashkent")
        self.ts = lambda h, m: int(datetime(2026, 9, 16, h, m, tzinfo=tz).timestamp())
        self.messages = [
            {"message_id": 1, "ts": self.ts(10, 1), "sender_id": 100, "sender_name": "Partner A", "username": "a", "text": "Когда будет выплата?", "reply_to_message_id": None},
            {"message_id": 2, "ts": self.ts(10, 3), "sender_id": 100, "sender_name": "Partner A", "username": "a", "text": "Проверьте пожалуйста", "reply_to_message_id": None},
            {"message_id": 3, "ts": self.ts(10, 8), "sender_id": 900, "sender_name": "Uzum Franchise", "username": "uzum_franchise", "text": "Выплата сегодня после 15:00", "reply_to_message_id": 1},
            {"message_id": 4, "ts": self.ts(11, 0), "sender_id": 101, "sender_name": "Partner B", "username": "b", "text": "Приложение не работает", "reply_to_message_id": None},
            {"message_id": 5, "ts": self.ts(11, 30), "sender_id": 900, "sender_name": "Uzum Franchise", "username": "uzum_franchise", "text": "Перезапустите приложение", "reply_to_message_id": 4},
            {"message_id": 6, "ts": self.ts(12, 0), "sender_id": 102, "sender_name": "Partner C", "username": "c", "text": "Поддержка, нужен договор аренды", "reply_to_message_id": None},
            {"message_id": 7, "ts": self.ts(18, 59), "sender_id": 700, "sender_name": "Shift Bot", "username": "opening_closing_bot", "text": "Смена скоро будет закрыта", "reply_to_message_id": None},
        ]

    def test_metrics(self):
        metrics = build_metrics(self.messages, self.config)
        self.assertEqual(
            (metrics["total_tickets"], metrics["responded"], metrics["unanswered"], metrics["sla_ok"]),
            (3, 2, 1, 1),
        )
        self.assertEqual(metrics["total_messages"], 4)
        self.assertAlmostEqual(metrics["sla_percent"], 100 / 3)
        self.assertAlmostEqual(metrics["median_minutes"], 18.5)
        self.assertAlmostEqual(metrics["faq_coverage_percent"], 100.0)
        self.assertTrue(metrics["agent_configured"])

    def test_sla_scope_excludes_partner_dialogue_chatter_and_automation(self):
        messages = [
            {"message_id": 20, "ts": self.ts(10, 0), "sender_id": 200, "sender_name": "Partner A", "username": "pa", "text": "@uzum_franchise, подскажите по выплате?", "reply_to_message_id": None},
            {"message_id": 21, "ts": self.ts(10, 1), "sender_id": 201, "sender_name": "Partner B", "username": "pb", "text": "У меня тоже", "reply_to_message_id": 20},
            {"message_id": 22, "ts": self.ts(10, 2), "sender_id": 202, "sender_name": "Partner C", "username": "pc", "text": "Добрый день всем", "reply_to_message_id": None},
            {"message_id": 23, "ts": self.ts(10, 3), "sender_id": 700, "sender_name": "Bot", "username": "other_bot", "is_bot": True, "text": "Автоматическое сообщение", "reply_to_message_id": None},
            {"message_id": 24, "ts": self.ts(10, 4), "sender_id": 900, "sender_name": "Uzum Franchise", "username": "uzum_franchise", "text": "Проверяем", "reply_to_message_id": 20},
            {"message_id": 25, "ts": self.ts(11, 0), "sender_id": 203, "sender_name": "Partner D", "username": "pd", "text": "Поддержка, почему приложение не работает?", "reply_to_message_id": None},
        ]
        metrics = build_metrics(messages, self.config)
        self.assertEqual(metrics["total_tickets"], 2)
        self.assertEqual(metrics["sla_ok"], 1)
        self.assertEqual(metrics["unanswered"], 1)
        self.assertAlmostEqual(metrics["sla_percent"], 50.0)
        self.assertEqual(metrics["excluded"], {
            "partner_dialogue": 1,
            "non_inquiry": 1,
            "automated": 1,
        })

    def test_shift_bot_sets_report_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MessageStore(str(Path(tmp) / "analytics.sqlite3"))
            updates = []
            raw_messages = [
                (100, self.ts(9, 55), 700, "opening_closing_bot", "Подготовка к смене"),
                (101, self.ts(10, 0), 700, "opening_closing_bot", "Смена открыта"),
                (102, self.ts(10, 5), 100, "partner_a", "Когда будет выплата?"),
                (103, self.ts(10, 9), 900, "uzum_franchise", "Сегодня после 15:00"),
                (104, self.ts(19, 0), 700, "opening_closing_bot", "Смена закрыта"),
                (105, self.ts(19, 5), 101, "partner_b", "Позднее сообщение"),
            ]
            for message_id, ts, sender_id, username, text in raw_messages:
                reply_to = 102 if message_id == 103 else None
                updates.append({
                    "message": {
                        "message_id": message_id,
                        "date": ts,
                        "text": text,
                        "chat": {"id": -1002707306458},
                        "from": {
                            "id": sender_id,
                            "first_name": username,
                            "username": username,
                            "is_bot": username == "opening_closing_bot",
                        },
                        **({"reply_to_message": {"message_id": reply_to}} if reply_to else {}),
                    }
                })
            store.record_updates(updates)
            service = DailyReportService(store, self.config)
            metrics = service.metrics_for_day(date(2026, 9, 16))
            self.assertEqual(metrics["shift_start"], "10:00")
            self.assertEqual(metrics["shift_end"], "19:00")
            self.assertEqual(metrics["shift_source"], "opening_closing_bot")
            self.assertEqual(metrics["total_tickets"], 1)

    def test_support_reply_to_another_message_does_not_close_latest_ticket(self):
        messages = [
            {"message_id": 30, "ts": self.ts(10, 0), "sender_id": 300, "sender_name": "Partner A", "username": "a", "text": "Поддержка, почему приложение не работает?", "reply_to_message_id": None},
            {"message_id": 31, "ts": self.ts(10, 1), "sender_id": 301, "sender_name": "Partner B", "username": "b", "text": "Добрый день", "reply_to_message_id": None},
            {"message_id": 32, "ts": self.ts(10, 4), "sender_id": 900, "sender_name": "Uzum Franchise", "username": "uzum_franchise", "text": "Ответ другому участнику", "reply_to_message_id": 31},
        ]
        metrics = build_metrics(messages, self.config)
        self.assertEqual(metrics["total_tickets"], 2)
        self.assertEqual(metrics["responded"], 1)
        self.assertEqual(metrics["unanswered"], 1)
        self.assertEqual(metrics["support_reply_messages"], 1)

    def test_direct_support_reply_qualifies_short_request(self):
        messages = [
            {"message_id": 40, "ts": self.ts(10, 0), "sender_id": 400, "sender_name": "Partner", "username": "p", "text": "Lichda javob bervoring", "reply_to_message_id": None},
            {"message_id": 41, "ts": self.ts(10, 2), "sender_id": 900, "sender_name": "Uzum Franchise", "username": "uzum_franchise", "text": "Javob berdik", "reply_to_message_id": 40},
        ]
        metrics = build_metrics(messages, self.config)
        self.assertEqual(metrics["total_tickets"], 1)
        self.assertEqual(metrics["responded"], 1)
        self.assertEqual(metrics["unanswered"], 0)
        self.assertEqual(metrics["sla_ok"], 1)
        self.assertEqual(metrics["support_reply_messages"], 1)
        self.assertEqual(metrics["linked_support_replies"], 1)

    def test_generic_partner_question_is_not_unanswered_support_ticket(self):
        messages = [
            {"message_id": 50, "ts": self.ts(10, 0), "sender_id": 500, "sender_name": "Partner", "username": "p", "text": "Nega savdo kam, bunga yordam berishmidimi?", "reply_to_message_id": None},
            {"message_id": 51, "ts": self.ts(10, 1), "sender_id": 501, "sender_name": "Partner 2", "username": "p2", "text": "100 mln foydami?", "reply_to_message_id": None},
        ]
        metrics = build_metrics(messages, self.config)
        self.assertEqual(metrics["total_tickets"], 0)
        self.assertEqual(metrics["unanswered"], 0)

    def test_advice_to_other_partners_is_not_support_request(self):
        messages = [
            {"message_id": 55, "ts": self.ts(10, 0), "sender_id": 550, "sender_name": "Partner", "username": "p", "text": "Hamkorlar yordam xizmati bo'limiga hamma shuni yozsin", "reply_to_message_id": None},
        ]
        metrics = build_metrics(messages, self.config)
        self.assertEqual(metrics["total_tickets"], 0)
        self.assertEqual(metrics["unanswered"], 0)

    def test_reports_missing_reply_targets_as_data_quality_issue(self):
        messages = [
            {"message_id": 60, "ts": self.ts(10, 5), "sender_id": 900, "sender_name": "Support", "username": "uzum_franchise", "text": "Ответ", "reply_to_message_id": 59},
            {"message_id": 61, "ts": self.ts(10, 6), "sender_id": 900, "sender_name": "Support", "username": "uzum_franchise", "text": "Дополнение", "reply_to_message_id": 59},
        ]
        metrics = build_metrics(messages, self.config)
        self.assertEqual(metrics["support_reply_messages"], 2)
        self.assertEqual(metrics["linked_support_replies"], 0)
        self.assertEqual(metrics["unlinked_support_replies"], 2)
        self.assertEqual(metrics["unlinked_reply_targets"], 1)

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


class ReportDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_is_russian_attached_and_explains_scope(self):
        config = ReportConfig(
            -1002707306458,
            8419189523,
            agent_usernames={"uzum_franchise"},
            service_bot_usernames={"opening_closing_bot"},
        )
        tz = ZoneInfo("Asia/Tashkent")
        opened = int(datetime(2026, 9, 17, 10, 0, tzinfo=tz).timestamp())
        answered = int(datetime(2026, 9, 17, 10, 4, tzinfo=tz).timestamp())

        with tempfile.TemporaryDirectory() as tmp:
            store = MessageStore(str(Path(tmp) / "analytics.sqlite3"))
            store.record_updates([
                {"message": {"message_id": 1, "date": opened, "text": "Когда будет выплата?", "chat": {"id": config.source_chat_id}, "from": {"id": 100, "first_name": "Partner", "username": "partner", "is_bot": False}}},
                {"message": {"message_id": 2, "date": answered, "text": "Сегодня", "chat": {"id": config.source_chat_id}, "from": {"id": 900, "first_name": "Support", "username": "uzum_franchise", "is_bot": False}, "reply_to_message": {"message_id": 1}}},
            ])

            class Client:
                captured = None

                async def send_report(self, chat_id, paths, caption):
                    self.captured = (chat_id, len(paths), caption, all(Path(p).exists() for p in paths))

            client = Client()
            service = DailyReportService(store, config)
            result = await service.send_day(
                client,
                date(2026, 9, 17),
                force=True,
                report_chat_id=8419189523,
            )

        self.assertTrue(result["sent"])
        self.assertEqual(client.captured[:2], (8419189523, 3))
        self.assertTrue(client.captured[3])
        self.assertIsNone(service.last_sent_day)
        caption = client.captured[2]
        self.assertLessEqual(len(caption), 1024)
        self.assertIn("@Ddmit05", caption)
        self.assertIn("Соблюдение SLA ≤ 15 мин", caption)
        self.assertIn("Покрытие FAQ", caption)
        self.assertIn("Reply-ответов поддержки", caption)
        self.assertIn("Корневые причины", caption)
        self.assertIn("Исключены диалоги партнёров", caption)


if __name__ == "__main__":
    unittest.main()
