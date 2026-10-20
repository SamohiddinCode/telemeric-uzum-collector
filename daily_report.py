from __future__ import annotations

import asyncio
import html
import logging
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from analytics_config import ReportConfig
from analytics_engine import build_commentary, build_metrics
from analytics_store import MessageStore
from report_renderer import ReportRenderer


TELEGRAM_CAPTION_SAFE_LIMIT = 1000
TELEGRAM_MESSAGE_SAFE_LIMIT = 3900
logger = logging.getLogger("telemeric.analytics")
REPORT_RETRY_INTERVAL = timedelta(minutes=5)
REPORT_RETRY_COUNT = 6  # Six retries after the initial attempt, through 19:30.


def _fit_commentary(prefix: str, commentary: str) -> str:
    """Keep an album caption below Telegram's 1024-character limit."""
    available = TELEGRAM_CAPTION_SAFE_LIMIT - len(prefix)
    if available <= 0:
        raise RuntimeError("Report caption prefix exceeds Telegram's safe limit")
    if len(commentary) <= available:
        return prefix + commentary
    return prefix + commentary[: available - 1].rstrip() + "…"


def _ticket_question(ticket: Any) -> str:
    return " ".join(
        str(message.get("text") or "").strip()
        for message in ticket.messages
        if str(message.get("text") or "").strip()
    ).strip()


def _message_link(chat_id: int, message_id: int) -> str:
    raw = str(abs(chat_id))
    internal_id = raw[3:] if raw.startswith("100") else raw
    return f"https://t.me/c/{internal_id}/{message_id}"


def build_unanswered_messages(
    metrics: dict[str, Any],
    config: ReportConfig,
    day: date,
) -> list[str]:
    """Build Telegram-safe HTML chunks listing every qualified unanswered ticket."""
    tickets = metrics.get("unanswered_tickets") or []
    if not tickets:
        return []

    header = (
        f"❗ <b>Обращения без первого ответа — {day.strftime('%d.%m.%Y')}</b>\n"
        f"Смена: {metrics.get('shift_start', config.workday_start)}–"
        f"{metrics.get('shift_end', config.workday_end)}. "
        "Включены только квалифицированные обращения партнёров к поддержке.\n\n"
    )
    entries: list[str] = []
    tz = ZoneInfo(config.timezone)
    for index, ticket in enumerate(tickets, 1):
        first = ticket.messages[0] if ticket.messages else {}
        username = str(first.get("username") or "").strip().lstrip("@")
        author = f"@{username}" if username else ticket.customer_name
        opened = datetime.fromtimestamp(ticket.opened_at, tz).strftime("%H:%M")
        question = _ticket_question(ticket) or "Текст обращения отсутствует"
        message_id = int(first.get("message_id") or 0)
        link = _message_link(config.source_chat_id, message_id) if message_id else ""
        link_line = f'\n<a href="{html.escape(link, quote=True)}">Открыть сообщение</a>' if link else ""
        entries.append(
            f"<b>{index}. {opened} · {html.escape(author)}</b>\n"
            f"{html.escape(question)}{link_line}\n\n"
        )

    chunks: list[str] = []
    current = header
    for entry in entries:
        if len(current) + len(entry) > TELEGRAM_MESSAGE_SAFE_LIMIT and current != header:
            chunks.append(current.rstrip())
            current = header + entry
        else:
            current += entry
    if current != header:
        chunks.append(current.rstrip())
    return chunks


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
    def __init__(self, store: MessageStore, config: ReportConfig, sheets_writer: Any = None) -> None:
        self.store, self.config = store, config
        self.sheets_writer = sheets_writer
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
            caption_prefix = (
                f"{mentions}\n"
                f"📊 <b>Отчёт SLA — {day.strftime('%d.%m.%Y')}</b>\n"
                f"🕒 Смена: <b>{metrics['shift_start']}–{metrics['shift_end']}</b> ({source_label})\n\n"
                f"Обращения партнёров: <b>{metrics['total_tickets']}</b>\n"
                f"Reply-ответов поддержки: <b>{metrics['support_reply_messages']}</b>; "
                f"связано: <b>{metrics['linked_support_replies']}</b>"
                + (
                    f"; без исходного: <b>{metrics['unlinked_reply_targets']}</b>\n"
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
                "• Только вопросы/просьбы партнёров к поддержке.\n"
                "• Исключены диалоги партнёров, не-обращения и автоматика.\n"
                f"• SLA % = ответы ≤ {self.config.sla_target_minutes} мин / все квалифицированные обращения × 100%.\n"
                "• Медиана — только обращения с ответом; FAQ — известные темы.\n\n"
                "💬 "
                )
            )
            caption = _fit_commentary(caption_prefix, commentary)
            recipient = report_chat_id or self.config.report_chat_id
            if hasattr(client, "send_report"):
                await client.send_report(recipient, paths, caption)
                for message in build_unanswered_messages(metrics, self.config, day):
                    await client.send_message(recipient, message)
            else:
                target = await client.get_entity(recipient)
                try:
                    await client.send_file(target, paths, caption=caption, parse_mode="html")
                except Exception:
                    await client.send_message(target, caption, parse_mode="html")
                    for path in paths:
                        await client.send_file(target, path)
                for message in build_unanswered_messages(metrics, self.config, day):
                    await client.send_message(target, message, parse_mode="html")
        sheet_saved = False
        if self.sheets_writer is not None:
            try:
                await self.sheets_writer.upsert_day(
                    day,
                    metrics,
                    self.config,
                    commentary,
                    recipient,
                )
                sheet_saved = True
            except Exception:
                logger.exception(
                    "Google Sheets write failed after Telegram report delivery for %s",
                    day,
                )
        # A manual/test delivery must not suppress the normal scheduled report.
        if not force:
            self.last_sent_day = day
        return {"sent": True, "metrics": metrics, "sheet_saved": sheet_saved}

    async def run(self, client: Any) -> None:
        tz = ZoneInfo(self.config.timezone)
        report_clock = parse_time(self.config.report_time)
        attempted_day: date | None = None
        attempted_slots: set[int] = set()
        while True:
            now = datetime.now(tz)
            if attempted_day != now.date():
                attempted_day = now.date()
                attempted_slots.clear()
            scheduled = datetime.combine(now.date(), report_clock, tzinfo=tz)
            last_attempt = scheduled + REPORT_RETRY_INTERVAL * REPORT_RETRY_COUNT

            if scheduled <= now <= last_attempt and self.last_sent_day != now.date():
                elapsed = now - scheduled
                slot = min(
                    int(elapsed.total_seconds() // REPORT_RETRY_INTERVAL.total_seconds()),
                    REPORT_RETRY_COUNT,
                )
                if slot not in attempted_slots:
                    attempted_slots.add(slot)
                    try:
                        result = await self.send_day(client, now.date())
                    except Exception:
                        logger.exception(
                            "Daily report attempt %s/%s failed for %s; will retry",
                            slot + 1,
                            REPORT_RETRY_COUNT + 1,
                            now.date(),
                        )
                    else:
                        if result.get("sent"):
                            logger.info("Daily report sent successfully for %s", now.date())
                        elif result.get("reason") == "already-sent":
                            self.last_sent_day = now.date()

            # Polling at most every 30 seconds gives a small tolerance for cold
            # starts while preserving a single attempt in each five-minute slot.
            await asyncio.sleep(30)
