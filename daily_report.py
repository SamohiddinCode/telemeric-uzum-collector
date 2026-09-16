from __future__ import annotations

import asyncio
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from analytics_config import ReportConfig
from analytics_engine import build_commentary, build_metrics
from analytics_store import MessageStore
from report_renderer import ReportRenderer


def parse_time(value: str) -> time:
    hour, minute = [int(x) for x in value.split(":", 1)]
    return time(hour, minute)


class DailyReportService:
    def __init__(self, store: MessageStore, config: ReportConfig) -> None:
        self.store, self.config = store, config
        self.renderer = ReportRenderer()
        self.last_sent_day: date | None = None

    def metrics_for_day(self, day: date) -> dict[str, Any]:
        tz = ZoneInfo(self.config.timezone)
        start = datetime.combine(day, parse_time(self.config.workday_start), tzinfo=tz)
        end = datetime.combine(day, parse_time(self.config.workday_end), tzinfo=tz)
        messages = self.store.get_messages(
            self.config.source_chat_id,
            int(start.timestamp()),
            int(end.timestamp()),
        )
        return build_metrics(messages, self.config)

    async def send_day(self, client: Any, day: date, force: bool = False) -> dict[str, Any]:
        if self.last_sent_day == day and not force:
            return {"sent": False, "reason": "already-sent"}
        metrics = self.metrics_for_day(day)
        commentary = build_commentary(metrics, self.config)
        with tempfile.TemporaryDirectory(prefix="telemeric-report-") as tmp:
            paths = [
                str(Path(tmp) / name)
                for name in ["01-summary.png", "02-charts.png", "03-details.png"]
            ]
            self.renderer.summary(metrics, self.config, day, paths[0])
            self.renderer.charts(metrics, self.config, day, paths[1])
            self.renderer.details(metrics, self.config, day, paths[2])
            target = await client.get_entity(self.config.report_chat_id)
            sla = "—" if metrics["sla_percent"] is None else f"{metrics['sla_percent']:.1f}%"
            median = (
                "—"
                if metrics["median_minutes"] is None
                else f"{metrics['median_minutes']:.1f} мин"
            )
            caption = (
                f"📊 <b>Daily SLA Report — {day.strftime('%d.%m.%Y')}</b>\n\n"
                f"Обращения: <b>{metrics['total_tickets']}</b>\n"
                f"SLA Compliance: <b>{sla}</b>\n"
                f"Median Response: <b>{median}</b>\n"
                f"Без ответа: <b>{metrics['unanswered']}</b>\n\n"
                f"💬 {commentary}"
            )
            try:
                await client.send_file(target, paths, caption=caption, parse_mode="html")
            except Exception:
                await client.send_message(target, caption, parse_mode="html")
                for path in paths:
                    await client.send_file(target, path)
        self.last_sent_day = day
        return {"sent": True, "metrics": metrics}

    async def run(self, client: Any) -> None:
        tz = ZoneInfo(self.config.timezone)
        report_clock = parse_time(self.config.report_time)
        while True:
            now = datetime.now(tz)
            scheduled = datetime.combine(now.date(), report_clock, tzinfo=tz)
            if now >= scheduled and self.last_sent_day != now.date():
                await self.send_day(client, now.date())
            await asyncio.sleep(30)
