from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Header, HTTPException, Query

import main as collector
from analytics_config import ReportConfig
from analytics_store import MessageStore
from daily_report import DailyReportService
from telegram_bot_sender import TelegramBotSender


logger = logging.getLogger("telemeric.analytics")
report_config = ReportConfig.from_env(collector.TARGET_CHAT_ID)
message_store = MessageStore(os.getenv("ANALYTICS_DB_PATH", "/tmp/telemeric/analytics.sqlite3"))
report_service = DailyReportService(message_store, report_config)
bot_sender = TelegramBotSender(collector.TELEGRAM_BOT_TOKEN) if collector.TELEGRAM_BOT_TOKEN else None

_original_deliver = collector.deliver
_original_lifespan = collector.app.router.lifespan_context


async def refresh_analytics_cache(day) -> int:
    """Refresh one local report day from the durable Site message store."""
    tz = ZoneInfo(report_config.timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day, datetime.max.time(), tzinfo=tz)
    payload = await collector.site_request(
        "GET",
        f"/api/telegram/analytics-export?from={int(start.timestamp())}&to={int(end.timestamp()) + 1}",
    )
    rows = payload.get("messages") or []
    updates = []
    for row in rows:
        sender_id = int(row.get("sender_id") or 0)
        automated = sender_id == 777000 or str(row.get("sender_name") or "").lower() == "telegram"
        support = bool(row.get("is_staff")) and not automated
        message = {
            "message_id": int(row.get("telegram_message_id") or 0),
            "date": int(row.get("sent_at") or 0),
            "text": str(row.get("text") or ""),
            "chat": {"id": int(row.get("chat_id") or 0), "type": "supergroup"},
            "from": {
                "id": sender_id,
                "first_name": str(row.get("sender_name") or ""),
                "username": "uzum_franchise" if support else ("telegram_service" if automated else ""),
                "is_bot": automated,
            },
        }
        if row.get("reply_to_message_id"):
            message["reply_to_message"] = {"message_id": int(row["reply_to_message_id"])}
        updates.append({"update_id": message["message_id"], "message": message})
    message_store.record_updates(updates)
    return len(updates)


async def deliver_with_analytics(updates):
    """Preserve existing site delivery, then mirror data into analytics cache."""
    await _original_deliver(updates)
    try:
        message_store.record_updates(updates)
    except Exception:
        logger.exception("Analytics cache write failed; collector delivery remains healthy")


async def report_runner() -> None:
    try:
        await refresh_analytics_cache(datetime.now(ZoneInfo(report_config.timezone)).date())
    except Exception:
        logger.exception("Analytics cache refresh failed; continuing with live bridge data")
    while True:
        if bot_sender:
            await report_service.run(bot_sender)
            return
        client = collector.collector_client
        if client and client.is_connected() and collector.collector_status.get("connected"):
            await report_service.run(client)
            return
        await asyncio.sleep(1)


collector.deliver = deliver_with_analytics
app = collector.app


@asynccontextmanager
async def analytics_lifespan(application):
    report_task = None
    async with _original_lifespan(application):
        if report_config.enabled:
            report_task = asyncio.create_task(report_runner())
        try:
            yield
        finally:
            if report_task:
                report_task.cancel()
                try:
                    await report_task
                except asyncio.CancelledError:
                    pass


app.router.lifespan_context = analytics_lifespan


def parse_report_date(value: str | None):
    tz = ZoneInfo(report_config.timezone)
    if not value:
        return datetime.now(tz).date()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


@app.get("/analytics/status")
async def analytics_status(
    token: str = Query(default=""),
    date: str | None = Query(default=None),
    authorization: str = Header(default=""),
):
    collector.require_setup_token(token, authorization)
    target_day = parse_report_date(date)
    try:
        await refresh_analytics_cache(target_day)
    except Exception:
        logger.exception("Analytics status refresh failed")
    metrics = report_service.metrics_for_day(target_day)
    return {
        "enabled": report_config.enabled,
        "deliveryMode": "bot" if bot_sender else "user-session",
        "sourceChatId": report_config.source_chat_id,
        "reportChatId": report_config.report_chat_id,
        "timezone": report_config.timezone,
        "workdayFallback": f"{report_config.workday_start}-{report_config.workday_end}",
        "detectedShift": f"{metrics['shift_start']}-{metrics['shift_end']}",
        "shiftSource": metrics["shift_source"],
        "reportTime": report_config.report_time,
        "slaTargetMinutes": report_config.sla_target_minutes,
        "slaTargetPercent": report_config.sla_target_percent,
        "supportAccounts": sorted(report_config.agent_usernames),
        "ignoredServiceBots": sorted(report_config.service_bot_usernames),
        "date": str(target_day),
        "metrics": {
            "messages": metrics["total_messages"],
            "tickets": metrics["total_tickets"],
            "responded": metrics["responded"],
            "supportReplyMessages": metrics["support_reply_messages"],
            "linkedSupportReplies": metrics["linked_support_replies"],
            "unanswered": metrics["unanswered"],
            "slaPercent": metrics["sla_percent"],
            "slaCompliant": metrics["sla_ok"],
            "medianResponseMinutes": metrics["median_minutes"],
            "faqCoveragePercent": metrics["faq_coverage_percent"],
            "rootCauses": metrics["root_causes"][:5],
            "excluded": metrics["excluded"],
        },
    }


@app.post("/analytics/restore")
async def analytics_restore(
    payload: dict,
    token: str = Query(default=""),
    authorization: str = Header(default=""),
):
    """Restore the disposable analytics cache without re-ingesting into the Site."""
    collector.require_setup_token(token, authorization)
    updates = payload.get("updates")
    if not isinstance(updates, list):
        raise HTTPException(status_code=400, detail="updates must be a list")
    if len(updates) > 5000:
        raise HTTPException(status_code=400, detail="too many updates")
    for update in updates:
        message = update.get("message") if isinstance(update, dict) else None
        chat = message.get("chat") if isinstance(message, dict) else None
        if not isinstance(chat, dict) or int(chat.get("id") or 0) != report_config.source_chat_id:
            raise HTTPException(status_code=400, detail="unexpected source chat")
    message_store.record_updates(updates)
    return {"restored": len(updates), "sourceChatId": report_config.source_chat_id}


@app.post("/analytics/report-now")
async def analytics_report_now(
    token: str = Query(default=""),
    date: str | None = Query(default=None),
    chat_id: int | None = Query(default=None),
    authorization: str = Header(default=""),
):
    collector.require_setup_token(token, authorization)
    client = bot_sender or collector.collector_client
    if not client or (
        not bot_sender
        and (not client.is_connected() or not collector.collector_status.get("connected"))
    ):
        raise HTTPException(status_code=503, detail="Telegram report sender is not connected")
    target_day = parse_report_date(date)
    try:
        await refresh_analytics_cache(target_day)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось обновить данные: {exc}") from exc
    recipient = chat_id or report_config.report_chat_id
    result = await report_service.send_day(
        client,
        target_day,
        force=True,
        report_chat_id=recipient,
    )
    metrics = result.get("metrics") or {}
    return {
        "sent": result.get("sent", False),
        "date": str(target_day),
        "shift": f"{metrics.get('shift_start', '—')}-{metrics.get('shift_end', '—')}",
        "shiftSource": metrics.get("shift_source"),
        "reportChatId": recipient,
        "tickets": metrics.get("total_tickets", 0),
        "slaPercent": metrics.get("sla_percent"),
        "medianResponseMinutes": metrics.get("median_minutes"),
        "faqCoveragePercent": metrics.get("faq_coverage_percent"),
        "unanswered": metrics.get("unanswered", 0),
    }


@app.get("/analytics/check-recipient")
async def analytics_check_recipient(
    token: str = Query(default=""),
    authorization: str = Header(default=""),
):
    collector.require_setup_token(token, authorization)
    if not bot_sender:
        raise HTTPException(status_code=503, detail="Telegram bot sender is not configured")
    try:
        chat = await bot_sender.check_chat(report_config.report_chat_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"reachable": True, "chat": chat}


@app.post("/analytics/test-delivery")
async def analytics_test_delivery(
    token: str = Query(default=""),
    authorization: str = Header(default=""),
):
    collector.require_setup_token(token, authorization)
    if not bot_sender:
        raise HTTPException(status_code=503, detail="Telegram bot sender is not configured")
    try:
        await bot_sender.send_message(
            report_config.report_chat_id,
            "✅ <b>Тестовый сервис подключён</b>\n\n"
            "Получатель отчётов подтверждён. Следующий этап — импорт реальных данных смены и отправка 3 PNG.",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"sent": True, "reportChatId": report_config.report_chat_id}
