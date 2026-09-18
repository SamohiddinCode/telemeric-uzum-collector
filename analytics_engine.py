from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analytics_config import ReportConfig


@dataclass
class Ticket:
    id: int
    customer_id: int
    customer_name: str
    opened_at: int
    last_customer_at: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    first_response_at: int | None = None
    first_response_text: str = ""
    agent_id: int | None = None
    agent_name: str = ""
    response_inferred: bool = False

    @property
    def response_minutes(self) -> float | None:
        if self.first_response_at is None:
            return None
        return max(0.0, (self.first_response_at - self.opened_at) / 60.0)


CATEGORY_RULES = [
    ("Выплаты / оплата", ("выплат", "оплат", "перевод", "деньг", "баланс", "payment", "tolov", "to'lov")),
    ("Инкассация", ("инкас", "inkass", "касс", "налич", "nalich")),
    ("Переоформление", ("переоформ", "юр лиц", "юрид", "qayta rasm")),
    ("Переезд", ("переезд", "ko'ch", "koch")),
    ("Документы", ("документ", "договор", "аренд", "ижар", "кадастр", "паспорт", "реквиз", "rekvizit")),
    ("Приложение / техника", ("ошиб", "не работает", "не откры", "прилож", "кабинет", "вход", "код", "error", "xato")),
    ("Оборудование", ("банкомат", "терминал", "принтер", "сканер", "оборуд")),
]

INQUIRY_MARKERS = (
    "?",
    "подскаж",
    "помог",
    "проверь",
    "уточн",
    "когда",
    "почему",
    "как ",
    "где ",
    "можно ли",
    "нужно",
    "нужен",
    "нужна",
    "не работает",
    "не откры",
    "не могу",
    "ошиб",
    "проблем",
    "iltimos",
    "yordam",
    "qachon",
    "qanday",
    "nega",
    "ishlam",
    "xato",
    "mumkinmi",
)

AUTOMATION_MARKERS = (
    "смена открыта",
    "смена закрыта",
    "автоматическое сообщение",
    "automatic message",
)


def _name(message: dict[str, Any]) -> str:
    return (
        message.get("sender_name")
        or ("@" + message["username"] if message.get("username") else "")
        or str(message.get("sender_id") or "Unknown")
    )


def _username(message: dict[str, Any]) -> str:
    return str(message.get("username") or "").lower().lstrip("@")


def _is_agent(message: dict[str, Any], config: ReportConfig) -> bool:
    username = _username(message)
    return int(message.get("sender_id") or 0) in config.agent_ids or bool(
        username and username in config.agent_usernames
    )


def _is_service_message(message: dict[str, Any], config: ReportConfig) -> bool:
    username = _username(message)
    return bool(message.get("is_bot")) or bool(
        username and username in config.service_bot_usernames
    )


def _scope_reason(
    message: dict[str, Any],
    messages_by_id: dict[int, dict[str, Any]],
    config: ReportConfig,
    support_reply_targets: set[int] | None = None,
) -> str:
    """Classify one message for partner-support SLA scope."""
    if _is_service_message(message, config):
        return "automated"
    if _is_agent(message, config):
        return "support"
    if not int(message.get("sender_id") or 0):
        return "invalid"
    text = str(message.get("text") or "").strip()
    if not text:
        return "non_inquiry"
    value = text.lower().replace("ё", "е")
    if any(marker in value for marker in AUTOMATION_MARKERS):
        return "automated"

    # A direct Telegram Reply from support is authoritative evidence that the
    # partner message belongs to the support queue.  Check it before the text
    # heuristics: short requests such as "личку посмотрите" otherwise look like
    # chatter and their real Reply is lost.
    message_id = int(message.get("message_id") or 0)
    if support_reply_targets and message_id in support_reply_targets:
        return "eligible"

    reply_id = int(message.get("reply_to_message_id") or 0)
    replied = messages_by_id.get(reply_id)
    if replied:
        if _is_agent(replied, config):
            return "eligible"
        if not _is_service_message(replied, config):
            return "partner_dialogue"

    if any(f"@{username}" in value for username in config.agent_usernames):
        return "eligible"
    if any(marker in value for marker in INQUIRY_MARKERS):
        return "eligible"
    return "non_inquiry"


def _canonical(text: str) -> str:
    value = text.lower().replace("ё", "е")
    value = re.sub(r"https?://\S+|[@#]\w+|\b\d{2,}\b", " ", value)
    value = re.sub(r"[^a-zа-я0-9ўқғҳ' ]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()[:180]


def _category(text: str) -> str:
    value = text.lower().replace("ё", "е")
    for label, keywords in CATEGORY_RULES:
        if any(keyword in value for keyword in keywords):
            return label
    return "Другое"


def build_tickets(
    messages: list[dict[str, Any]],
    config: ReportConfig,
    classification: Counter[str] | None = None,
) -> list[Ticket]:
    tickets: list[Ticket] = []
    by_message: dict[int, Ticket] = {}
    active: dict[int, Ticket] = {}
    gap = config.ticket_gap_minutes * 60
    messages_by_id = {
        int(message["message_id"]): message
        for message in messages
        if message.get("message_id")
    }
    support_reply_targets = {
        int(message.get("reply_to_message_id") or 0)
        for message in messages
        if _is_agent(message, config) and message.get("reply_to_message_id")
    }

    for message in messages:
        reason = _scope_reason(message, messages_by_id, config, support_reply_targets)
        if classification is not None:
            classification[reason] += 1
        if reason in {"automated", "invalid", "partner_dialogue", "non_inquiry"}:
            continue
        sender_id = int(message.get("sender_id") or 0)
        ts = int(message.get("ts") or 0)
        if reason == "support":
            target = by_message.get(int(message.get("reply_to_message_id") or 0))
            if target and target.first_response_at is None:
                target.first_response_at = ts
                target.first_response_text = str(message.get("text") or "")
                target.agent_id = sender_id
                target.agent_name = _name(message)
                target.response_inferred = False
            continue

        ticket = active.get(sender_id)
        if ticket is None or ticket.first_response_at is not None or ts - ticket.last_customer_at > gap:
            ticket = Ticket(len(tickets) + 1, sender_id, _name(message), ts, ts)
            tickets.append(ticket)
            active[sender_id] = ticket
        ticket.messages.append(message)
        ticket.last_customer_at = ts
        if message.get("message_id"):
            by_message[int(message["message_id"])] = ticket
    return tickets


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] if lo == hi else ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def build_metrics(messages: list[dict[str, Any]], config: ReportConfig) -> dict[str, Any]:
    classification: Counter[str] = Counter()
    tickets = build_tickets(messages, config, classification)
    responded = [t for t in tickets if t.response_minutes is not None]
    response_times = [t.response_minutes for t in responded if t.response_minutes is not None]
    sla_ok = [t for t in responded if (t.response_minutes or 0) <= config.sla_target_minutes]
    unanswered = [t for t in tickets if t.first_response_at is None]
    categories: Counter[str] = Counter()
    questions: Counter[str] = Counter()
    solutions: Counter[str] = Counter()
    q_display: dict[str, str] = {}
    s_display: dict[str, str] = {}
    hourly: Counter[int] = Counter()
    agents: Counter[str] = Counter()
    delays: list[Ticket] = []
    tz = ZoneInfo(config.timezone)

    for ticket in tickets:
        question = " ".join(str(m.get("text") or "") for m in ticket.messages).strip()
        category = _category(question)
        categories[category] += 1
        key = _canonical(question)
        if key:
            questions[key] += 1
            q_display.setdefault(key, question[:220])
        hourly[datetime.fromtimestamp(ticket.opened_at, tz).hour] += 1
        if ticket.first_response_text:
            key = _canonical(ticket.first_response_text)
            if key:
                solutions[key] += 1
                s_display.setdefault(key, ticket.first_response_text[:220])
        if ticket.agent_name:
            agents[ticket.agent_name] += 1
        if ticket.response_minutes is not None and ticket.response_minutes > config.sla_target_minutes:
            delays.append(ticket)

    delays.sort(key=lambda t: t.response_minutes or 0, reverse=True)
    messages_by_id = {
        int(message["message_id"]): message
        for message in messages
        if message.get("message_id")
    }
    support_reply_messages = [
        message
        for message in messages
        if _is_agent(message, config) and message.get("reply_to_message_id")
    ]
    linked_support_replies = sum(
        1
        for message in support_reply_messages
        if int(message.get("reply_to_message_id") or 0) in messages_by_id
    )
    faq_covered = sum(count for label, count in categories.items() if label != "Другое")
    total_tickets = len(tickets)
    root_causes = [
        {
            "label": label,
            "count": count,
            "percent": count / total_tickets * 100 if total_tickets else 0.0,
        }
        for label, count in categories.most_common()
    ]
    return {
        "tickets": tickets,
        "total_tickets": total_tickets,
        "total_messages": sum(len(ticket.messages) for ticket in tickets),
        "responded": len(responded),
        "support_reply_messages": len(support_reply_messages),
        "linked_support_replies": linked_support_replies,
        "unanswered": len(unanswered),
        "sla_ok": len(sla_ok),
        "sla_breaches": total_tickets - len(sla_ok),
        "sla_percent": (len(sla_ok) / total_tickets * 100) if total_tickets else None,
        "average_minutes": (sum(response_times) / len(response_times)) if response_times else None,
        "median_minutes": _percentile(response_times, 0.5),
        "p90_minutes": _percentile(response_times, 0.9),
        "faq_covered": faq_covered,
        "faq_coverage_percent": (faq_covered / total_tickets * 100) if total_tickets else None,
        "categories": categories.most_common(),
        "root_causes": root_causes,
        "hourly": sorted(hourly.items()),
        "agents": agents.most_common(),
        "top_questions": [{"text": q_display[k], "count": c} for k, c in questions.most_common(7)],
        "top_solutions": [{"text": s_display[k], "count": c} for k, c in solutions.most_common(5)],
        "delays": delays[:7],
        "excluded": {
            "partner_dialogue": classification["partner_dialogue"],
            "non_inquiry": classification["non_inquiry"],
            "automated": classification["automated"],
        },
        "agent_configured": bool(config.agent_ids or config.agent_usernames),
    }


def build_commentary(metrics: dict[str, Any], config: ReportConfig) -> str:
    if not metrics["agent_configured"]:
        return (
            "SLA пока не рассчитывается: не настроен общий аккаунт поддержки. "
            "Остальная статистика уже собирается."
        )
    if not metrics["total_tickets"]:
        return "За выбранную смену обращений не найдено."
    sla = metrics["sla_percent"]
    parts = [
        "Нет обращений, соответствующих критериям SLA."
        if sla is None
        else f"Соблюдение SLA: {sla:.1f}% при нормативе ответа ≤ {config.sla_target_minutes} мин."
    ]
    faq = metrics["faq_coverage_percent"]
    if faq is not None:
        parts.append(f"Покрытие FAQ: {faq:.1f}% обращений отнесено к известным темам.")
    if metrics["categories"]:
        label, count = metrics["categories"][0]
        parts.append(f"Главная тема — «{label}» ({count} обращ.).")
    if metrics["hourly"]:
        hour, count = max(metrics["hourly"], key=lambda x: x[1])
        parts.append(f"Пик — {hour:02d}:00–{(hour + 1) % 24:02d}:00 ({count} обращ.).")
    if metrics["unanswered"]:
        parts.append(f"Без первого ответа: {metrics['unanswered']}.")
    if metrics["delays"]:
        parts.append(f"Нужно разобрать {len(metrics['delays'])} самых долгих SLA-просрочек.")
    return " ".join(parts)
