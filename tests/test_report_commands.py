import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import bootstrap


class ReportCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sender = AsyncMock()
        self.sender.send_message = AsyncMock()
        bootstrap.handled_report_command_updates.clear()

    def update(self, user_id, text):
        return {"update_id": 9001, "message": {
            "message_id": 71, "text": text,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id},
        }}

    async def test_only_approved_users_can_send_group_report(self):
        with patch.object(bootstrap, "bot_sender", self.sender), patch.object(
            bootstrap.report_config, "report_operator_ids", {8419189523, 7952444345}
        ), patch.object(bootstrap, "_send_private_report", new_callable=AsyncMock) as send:
            await bootstrap.handle_report_commands([self.update(
                1234, bootstrap.GROUP_REPORT_BUTTON + "28.10.2026"
            )])
            send.assert_not_awaited()
            for user_id in (8419189523, 7952444345):
                bootstrap.handled_report_command_updates.clear()
                await bootstrap.handle_report_commands([self.update(
                    user_id, bootstrap.GROUP_REPORT_BUTTON + "28.10.2026"
                )])
                send.assert_awaited_with(user_id, date(2026, 10, 28), to_group=True)

    async def test_group_destination_is_fixed_and_schedule_is_not_suppressed(self):
        send = AsyncMock(return_value={"sent": True})
        with patch.object(bootstrap, "bot_sender", self.sender), patch.object(
            bootstrap, "refresh_analytics_cache", new_callable=AsyncMock
        ), patch.object(bootstrap.report_service, "send_day", send), patch.object(
            bootstrap.report_config, "report_group_chat_id", -5271279516
        ):
            await bootstrap._send_private_report(7952444345, date(2026, 10, 28), to_group=True)
            send.assert_awaited_once_with(
                self.sender, date(2026, 10, 28), force=True, report_chat_id=-5271279516
            )
            self.assertIn("28.10.2026", self.sender.send_message.call_args.kwargs[
                "reply_markup"]["keyboard"][0][0]["text"])
