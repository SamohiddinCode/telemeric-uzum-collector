import time
import unittest
from pathlib import Path

from faq_shadow import FAQKnowledgeBase, FAQShadowAssistant


ROOT = Path(__file__).resolve().parents[1]


class FakeSender:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class FAQKnowledgeBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = FAQKnowledgeBase(ROOT / "faq_knowledge_base.json")

    def test_loads_only_active_entries(self):
        self.assertEqual(len(self.kb.entries), 40)
        self.assertNotIn(5, {entry["id"] for entry in self.kb.entries})

    def test_matches_common_russian_questions(self):
        samples = {
            "Инкассатор не приехал, что делать?": 40,
            "Почему наш ПВЗ не виден на карте?": 2,
            "Как поменять банковские реквизиты?": 37,
            "Можно поставить банкомат в ПВЗ?": 3,
            "Какие товары можно заказать через JIRA?": 29,
        }
        for question, expected_id in samples.items():
            with self.subTest(question=question):
                match = self.kb.match(question)
                self.assertIsNotNone(match)
                self.assertEqual(match.entry["id"], expected_id)

    def test_matches_uzbek_question(self):
        match = self.kb.match("Inkassator kelmadi, nima qilaman?")
        self.assertIsNotNone(match)
        self.assertEqual(match.entry["id"], 40)
        self.assertEqual(match.language, "uz")

    def test_rejects_unrelated_chat(self):
        self.assertIsNone(self.kb.match("Добрый вечер, коллеги"))


class FAQShadowAssistantTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sender = FakeSender()
        self.assistant = FAQShadowAssistant(
            FAQKnowledgeBase(ROOT / "faq_knowledge_base.json"),
            self.sender,
            source_chat_id=-1002707306458,
            recipient_id=8419189523,
            ignored_usernames={"uzum_franchise", "opening_closing_bot"},
        )

    async def test_sends_private_preview_and_never_group_reply(self):
        update = {
            "update_id": 1,
            "message": {
                "message_id": 77,
                "date": int(time.time()),
                "text": "Инкассатор не приехал, что делать?",
                "chat": {"id": -1002707306458, "type": "supergroup"},
                "from": {"id": 101, "first_name": "Partner", "username": "partner", "is_bot": False},
            },
        }
        sent = await self.assistant.handle_updates([update])
        self.assertEqual(sent, 1)
        self.assertEqual(len(self.sender.messages), 1)
        chat_id, text = self.sender.messages[0]
        self.assertEqual(chat_id, 8419189523)
        self.assertIn("Инкассатор не приехал", text)
        self.assertIn("Предлагаемый ответ", text)

    async def test_ignores_support_bot_old_and_duplicate_updates(self):
        base = {
            "message_id": 78,
            "date": int(time.time()),
            "text": "Инкассатор не приехал",
            "chat": {"id": -1002707306458, "type": "supergroup"},
            "from": {"id": 102, "username": "uzum_franchise", "is_bot": False},
        }
        self.assertEqual(await self.assistant.handle_updates([{"message": base}]), 0)

        base["from"] = {"id": 103, "username": "partner", "is_bot": False}
        base["date"] = int(time.time()) - 3600
        self.assertEqual(await self.assistant.handle_updates([{"message": base}]), 0)

        base["date"] = int(time.time())
        self.assertEqual(await self.assistant.handle_updates([{"message": base}]), 1)
        self.assertEqual(await self.assistant.handle_updates([{"message": base}]), 0)


if __name__ == "__main__":
    unittest.main()
