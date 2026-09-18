from __future__ import annotations

import asyncio
import tempfile
from datetime import date, datetime, time, timedelta
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


def _username(message: dict[str, Any]) -> str:
    return str(message.get("username") or "").lower().lstrip("@")


def _is_shift_bot(message: dict[str, Any], config: ReportConfig) -> bool:
    username = _username(message)
    return bool(username and username in config.service_bot_usernames)


def _looks_like_open(text: str) -> bool:
    value = text.lower().replace("ё", "е")
    return any(token in value for token in ("откр", "смена откры", "open", "ochild", "ochildi", "ish bosh"))


def _looks_like_close(text: str) -> bool:
    value = text.lower().replace("ё", "е")
    return any(token in value for token in ("закр", "смена закр", "close", "yopil", "yopildi", "ish yakun"))


class DailyReportService:
    def __init__(self, store: MessageStore, config: ReportConfig) -> None:
        self.store, self.config = store, config
        self.renderer = ReportRenderer()
        self.last_sent_day: date | None = None

    def _configured_window(self, day: date) -> tuple[int, int]:
        tz = ZoneInfo(self.config.timezone)
        start = datetime.combine(day, parse_time(self.config.workday_start), tzinfo=tz)
        end = datetime.combine(day, parse_time(self.config.workday_end), tzinfo=tz)
        return int(start.timestamp()), int(end.timestamp())

    def _shift_window(self, day: date) -> tuple[int, int, str]:
        tz = ZoneInfo(self.config.timezone)
        day_start = datetime.combine(day, time.min, tzinfo=tz)
        next_day = day_start + timedelta(days=1)
        day_messages = self.store.get_messages(
            self.config.source_chat_id,
            int(day_start.timestamp()),
            int(next_day.timestamp()),
        )
        openings: list[int] = []
        closings: list[int] = []
        for message in day_messages:
            if not _is_shift_bot(message, self.config):
                continue
            text = str(message.get("text") or "")
            ts = int(message.get("ts") or 0)
            if _looks_like_open(text):
                openings.append(ts)
            if _looks_like_close(text):
                closings.append(ts)

        if openings:
            start_ts = min(openings)
            close_after_open = [ts for ts in closings if ts > start_ts]
            if close_after_open:
                return start_ts, min(close_after_open), "opening_closing_bot"

        start_ts, end_ts = self._configured_window(day)
        return start_ts, end_ts, "configured"

    def metrics_for_day(self, day: date) -> dict[str, Any]:
        start_ts, end_ts, source = self._shift_window(day)
        messages = self.store.get_messages(
            self.config.source_chat_id,
            start_ts,
            end_ts,
        )
        metrics = build_metrics(messages, self.config)
        tz = ZoneInfo(self.config.timezone)
        metrics["shift_start"] = datetime.fromtimestamp(start_ts, tz).strftime("%H:%M")
        metrics["shift_end"] = datetime.fromtimestamp(end_ts, tz).strftime("%H:%M")
        metrics["shift_source"] = source
        return metrics

    async def send_day(
        self,
        client: Any,
        day: date,
        force: bool = False,
        report_chat_id: int | None = None,
    ) -> dict[str, Any]:
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
            sla = "—" if metrics["sla_percent"] is None else f"{metrics['sla_percent']:.1f}%"
            median = (
                "—"
                if metrics["median_minutes"] is None
                else f"{metrics['median_minutes']:.1f} мин"
            )
            source_label = "по сообщениям бота" if metrics["shift_source"] == "opening_closing_bot" else "по расписанию"
            faq = (
                "—"
                if metrics["faq_coverage_percent"] is None
                else f"{metrics['faq_coverage_percent']:.1f}%"
            )
            causes = ", ".join(
                f"{item['label']} — {item['count']}"
                for item in metrics["root_causes"][:3]
            ) or "нет квалифицированных обращений"
            mentions = " ".join(self.config.report_mentions)
            caption = (
                f"{mentions}\n"
                f"📊 <b>Отчёт SLA — {day.strftime('%d.%m.%Y')}</b>\n"
                f"🕒 Смена: <b>{metrics['shift_start']}–{metrics['shift_end']}</b> ({source_label})\n\n"
                f"Обращения партнёров: <b>{metrics['total_tickets']}</b>\n"
                f"Reply-ответов поддержки: <b>{metrics['support_reply_messages']}</b>\n"
                f"Связано с исходными обращениями: <b>{metrics['linked_support_replies']}</b>"
                + (
                    f"; не найдено исходных сообщений: <b>{metrics['unlinked_reply_targets']}</b>\n"
                    if metrics["unlinked_reply_targets"]
                    else "\n"
                )
                + (
                f"Соблюдение SLA ≤ {self.config.sla_target_minutes} мин: <b>{sla}</b> "
                f"({metrics['sla_ok']} вовремя из {metrics['total_tickets']})\n"
                f"Медиана первого ответа: <b>{median}</b>\n"
                f"Покрытие FAQ: <b>{faq}</b>\n"
                f"Без ответа: <b>{metrics['unanswered']}</b>\n"
                f"Нарушения SLA всего: <b>{metrics['sla_breaches']}</b> "
                f"(ответ позже {self.config.sla_target_minutes} мин: {len(metrics['delays'])}, "
                f"без ответа: {metrics['unanswered']})\n"
                f"Корневые причины: {causes}\n\n"
                "<b>Критерии расчёта</b>\n"
                "• Включены только вопросы/просьбы партнёров к поддержке.\n"
                "• Исключены диалоги партнёров между собой, не-обращения и автоматические сообщения.\n"
                f"• SLA % = ответы ≤ {self.config.sla_target_minutes} мин / все квалифицированные обращения × 100%.\n"
                "• Медиана считается только по обращениям с зафиксированным ответом.\n"
                "• FAQ Coverage — доля обращений, отнесённых к известной теме.\n\n"
                f"💬 {commentary}"
                )
            )
            recipient = report_chat_id or self.config.report_chat_id
            if hasattr(client, "send_report"):
                await client.send_report(recipient, paths, caption)
            else:
                target = await client.get_entity(recipient)
                try:
                    await client.send_file(target, paths, caption=caption, parse_mode="html")
                except Exception:
                    await client.send_message(target, caption, parse_mode="html")
                    for path in paths:
                        await client.send_file(target, path)
        # A manual/test delivery must not suppress the normal scheduled report.
        if not force:
            self.last_sent_day = day
        return {"sent": True, "metrics": metrics}

    async def run(self, client: Any) -> None:
        tz = ZoneInfo(self.config.timezone)
        report_clock = parse_time(self.config.report_time)
        while True:
            now = datetime.now(tz)
            scheduled = datetime.combine(now.date(), report_clock, tzinfo=tz)
            # Do not resend today's report merely because Render restarted long
            # after the scheduled run.  A ten-minute delivery window still
            # tolerates brief deploys or cold starts around report time.
            delivery_deadline = scheduled + timedelta(minutes=10)
            if scheduled <= now < delivery_deadline and self.last_sent_day != now.date():
                await self.send_day(client, now.date())
            elif now >= delivery_deadline and self.last_sent_day is None:
                self.last_sent_day = now.date()
            await asyncio.sleep(30)
