from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx

from analytics_config import ReportConfig


class GoogleSheetsWriter:
    """Upsert one aggregate daily-report row through a Google Apps Script webhook."""

    def __init__(
        self,
        webhook_url: str,
        secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.secret = secret
        self.client = client

    @classmethod
    def from_env(cls) -> "GoogleSheetsWriter | None":
        url = os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "").strip()
        secret = os.getenv("GOOGLE_SHEETS_WEBHOOK_SECRET", "").strip()
        if not url or not secret:
            return None
        return cls(url, secret)

    async def upsert_day(
        self,
        day: date,
        metrics: dict[str, Any],
        config: ReportConfig,
        commentary: str,
        report_chat_id: int,
    ) -> dict[str, Any]:
        payload = {
            "secret": self.secret,
            "report": {
                "date": day.isoformat(),
                "shiftStart": metrics.get("shift_start"),
                "shiftEnd": metrics.get("shift_end"),
                "shiftSource": metrics.get("shift_source"),
                "tickets": metrics.get("total_tickets"),
                "responded": metrics.get("responded"),
                "slaCompliant": metrics.get("sla_ok"),
                "slaPercent": metrics.get("sla_percent"),
                "slaTargetMinutes": config.sla_target_minutes,
                "medianResponseMinutes": metrics.get("median_minutes"),
                "faqCoveragePercent": metrics.get("faq_coverage_percent"),
                "unanswered": metrics.get("unanswered"),
                "slaBreaches": metrics.get("sla_breaches"),
                "delayedReplies": len(metrics.get("delays") or []),
                "supportReplyMessages": metrics.get("support_reply_messages"),
                "linkedSupportReplies": metrics.get("linked_support_replies"),
                "unlinkedReplyTargets": metrics.get("unlinked_reply_targets"),
                "rootCauses": metrics.get("root_causes") or [],
                "excluded": metrics.get("excluded") or {},
                "commentary": commentary,
                "reportChatId": report_chat_id,
            },
        }

        if self.client is not None:
            response = await self.client.post(self.webhook_url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.post(self.webhook_url, json=payload)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Google Sheets webhook rejected report: {result}")
        return result
